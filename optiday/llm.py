"""Talking to the model you control: Ollama (on this Mac or your private VM) or
any OpenAI-compatible server you host. No third-party cloud is ever contacted
unless you point base_url at one yourself."""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("optiday.llm")


class LLMError(RuntimeError):
    pass


def _opener(url: str):
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def parse_json(text: str):
    """Best-effort JSON extraction from model output."""
    if text is None:
        return None
    t = text.strip()
    t = re.sub(r"^<think>.*?</think>\s*", "", t, flags=re.S)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    return None


class LLM:
    name = "llm"

    def __init__(self, base_url: str, model: str, timeout: float = 240, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.api_key = api_key

    def _post(self, path: str, body: dict) -> dict:
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with _opener(url).open(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            raise LLMError(f"{url}: {e}") from e

    def _get(self, path: str) -> dict:
        url = self.base_url + path
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        req = urllib.request.Request(url, headers=headers)
        try:
            with _opener(url).open(req, timeout=5) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            raise LLMError(f"{url}: {e}") from e

    def chat(self, system: str, user: str, want_json: bool = True) -> str:
        raise NotImplementedError

    def chat_json(self, system: str, user: str):
        try:
            out = parse_json(self.chat(system, user, want_json=True))
        except LLMError as e:
            log.warning("AI request failed: %s", e)
            return None
        if out is None:
            log.warning("AI returned something that wasn't JSON")
        return out

    def status(self) -> tuple[bool, str]:
        raise NotImplementedError


class OllamaLLM(LLM):
    name = "ollama"

    def chat(self, system: str, user: str, want_json: bool = True) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "keep_alive": "2m",  # free the model's memory soon after the batch
        }
        if want_json:
            body["format"] = "json"
        r = self._post("/api/chat", body)
        return (r.get("message") or {}).get("content", "")

    def status(self) -> tuple[bool, str]:
        try:
            tags = self._get("/api/tags")
        except LLMError as e:
            return False, f"Can't reach Ollama at {self.base_url} ({e.__cause__ or e})"
        names = {m.get("name", "") for m in tags.get("models", [])}
        want = self.model if ":" in self.model else f"{self.model}:latest"
        if want in names or self.model in names:
            return True, f"Ollama is running with {self.model}"
        return False, f"Ollama is running but model '{self.model}' isn't downloaded (run: ollama pull {self.model})"


class OpenAICompatLLM(LLM):
    name = "openai"

    def chat(self, system: str, user: str, want_json: bool = True) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        if want_json:
            body["response_format"] = {"type": "json_object"}
        path = "/chat/completions" if self.base_url.endswith("/v1") else "/v1/chat/completions"
        r = self._post(path, body)
        try:
            return r["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected response: {str(r)[:200]}") from e

    def status(self) -> tuple[bool, str]:
        path = "/models" if self.base_url.endswith("/v1") else "/v1/models"
        try:
            self._get(path)
        except LLMError as e:
            return False, f"Can't reach the AI server at {self.base_url} ({e.__cause__ or e})"
        return True, f"AI server reachable at {self.base_url} ({self.model})"


def make_llm(cfg) -> LLM | None:
    ai = cfg.ai
    provider = str(ai.get("provider", "ollama")).lower()
    if provider in ("none", "off", "false", ""):
        return None
    cls = OpenAICompatLLM if provider in ("openai", "openai-compatible", "vllm", "llamacpp", "lmstudio") \
        else OllamaLLM
    return cls(ai.get("base_url", "http://127.0.0.1:11434"), ai.get("model", "qwen2.5:7b"),
               float(ai.get("timeout_seconds", 240)), ai.get("api_key", ""))
