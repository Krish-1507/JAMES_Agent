"""Concrete LLM providers.

* ``OpenAICompatibleProvider`` covers OpenAI, OpenRouter, Groq and any
  OpenAI-compatible ``custom`` endpoint (Ollama, LM Studio, vLLM, Together...).
* ``AnthropicProvider`` and ``GeminiProvider`` use their native SDKs.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .base import LLMProvider, LLMResponse, Message, Tool, ToolCall


def _image_payload(image: str) -> tuple[str, str]:
    """Return (media_type, base64-data) for an image reference.

    Accepts data URIs (``data:image/png;base64,...``), local file paths, and
    raw base64 (assumed PNG).
    """
    if image.startswith("data:"):
        head, _, b64 = image.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return mime, b64
    if image.startswith(("http://", "https://")):
        raise ValueError("http(s) images must be fetched before encoding")
    path = Path(image)
    if path.exists():
        mime = "image/png"
        suffix = path.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"
        elif suffix == ".gif":
            mime = "image/gif"
        return mime, base64.b64encode(path.read_bytes()).decode("ascii")
    return "image/png", image

# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenAI / OpenRouter / Groq / custom)
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(LLMProvider):
    name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        timeout: int = 120,
        extra_headers: dict[str, str] | None = None,
    ):
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_headers = extra_headers or {}
        self._client = OpenAI(
            api_key=api_key or "sk-noauth",
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )

    def validate(self) -> None:
        if not getattr(self._client, "api_key", "") or self._client.api_key == "sk-noauth":
            raise RuntimeError("Missing API key for this OpenAI-compatible provider.")

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        images: list[str] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        effective_model = model or self.model
        kwargs: dict[str, Any] = dict(
            model=effective_model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            extra_headers=self.extra_headers,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        elif images:
            # Vision: attach images to the last user message as multimodal content.
            multimodal = []
            for i, m in enumerate(messages):
                if m.get("role") == "user" and i == len(messages) - 1:
                    content = [{"type": "text", "text": m.get("content", "")}]
                    for img in images:
                        url = img if str(img).startswith("http") else f"data:image/png;base64,{img}"
                        content.append({"type": "image_url", "image_url": {"url": url}})
                    multimodal.append({**m, "content": content})
                else:
                    multimodal.append(m)
            kwargs["messages"] = multimodal

        resp = self._client.chat.completions.create(**kwargs)
        if not getattr(resp, "choices", None):
            err = getattr(resp, "error", None) or {}
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"Provider returned no completion choice. {detail}".strip())
        choice = resp.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            raw=resp,
            finish_reason=choice.finish_reason,
        )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        timeout: int = 120,
    ):
        import anthropic

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    def validate(self) -> None:
        if not self._client.api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY.")

    @staticmethod
    def _attach_images(messages: list[Message], images: list[str]) -> list[Message]:
        """Attach images to the last user message as Anthropic image blocks."""
        out = list(messages)
        for i in range(len(out) - 1, -1, -1):
            m = out[i]
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, list):
                blocks = list(content)
            elif content:
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = []
            for img in images:
                if img.startswith(("http://", "https://")):
                    blocks.append(
                        {"type": "image", "source": {"type": "url", "url": img}}
                    )
                else:
                    media_type, data = _image_payload(img)
                    blocks.append(
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": data},
                        }
                    )
            out[i] = {**m, "content": blocks}
            break
        return out

    @staticmethod
    def _to_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return out

    @staticmethod
    def _split_system(messages: list[Message]):
        system = None
        cleaned: list[Message] = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
                continue
            cleaned.append(m)
        return system, cleaned

    def _to_anthropic_messages(self, messages: list[Message]):
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id"),
                                "content": content,
                            }
                        ],
                    }
                )
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m.get("tool_calls", []):
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": fn.get("name"),
                            "input": args,
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": role, "content": content})
        return out

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        images: list[str] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        system, conv = self._split_system(messages)
        if images:
            conv = self._attach_images(conv, images)
        anthropic_messages = self._to_anthropic_messages(conv)
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=anthropic_messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._to_anthropic_tools(tools)
            kwargs["tool_choice"] = {"type": "auto" if tool_choice == "auto" else "any"}

        resp = self._client.messages.create(**kwargs)
        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        return LLMResponse(
            content=content_text, tool_calls=tool_calls, raw=resp, finish_reason=resp.stop_reason
        )


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        timeout: int = 120,
    ):
        # Uses the current `google.genai` SDK (the older `google.generativeai`
        # package is end-of-life as of 2026). The client is built lazily because
        # the SDK rejects an empty API key at construction time.
        from google import genai
        from google.genai import types as genai_types

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._api_key = api_key
        self._timeout = timeout
        self._types = genai_types
        self._genai = genai
        self._client = None

    def validate(self) -> None:
        if not self._api_key:
            raise RuntimeError("Missing GEMINI_API_KEY.")

    def _get_client(self):
        if self._client is None:
            # Bound the call like the other providers: the genai SDK default
            # (120s timeout, 5 retry attempts with exponential backoff) can
            # stall a failover chain well past its budget.
            self._client = self._genai.Client(
                api_key=self._api_key,
                http_options=self._types.HttpOptions(
                    timeout=self._timeout * 1000,
                    retry_options=self._types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    def _to_gemini_tools(self, tools: list[Tool]) -> list:
        T = self._types
        declarations = []
        for t in tools:
            fn = t.get("function", t)
            declarations.append(
                T.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters", {"type": "object", "properties": {}}),
                )
            )
        return [T.Tool(function_declarations=declarations)]

    def _to_gemini_contents(self, messages: list[Message], images: list[str] | None = None):
        T = self._types
        mapping = {"user": "user", "assistant": "model", "tool": "user", "system": "user"}
        contents = []
        last_user_idx = max(
            (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
        )
        for i, m in enumerate(messages):
            role = mapping.get(m["role"], "user")
            if m["role"] == "tool":
                contents.append(
                    T.Content(
                        role="user",
                        parts=[T.Part(text=f"[tool result] {m.get('content', '')}")],
                    )
                )
            elif m["role"] == "assistant" and m.get("tool_calls"):
                parts: list = []
                if m.get("content"):
                    parts.append(T.Part(text=m["content"]))
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parts.append(
                        T.Part(function_call=T.FunctionCall(name=fn.get("name"), args=args))
                    )
                contents.append(T.Content(role="model", parts=parts))
            else:
                parts: list = [T.Part(text=str(m.get("content", "")))]
                if images and i == last_user_idx:
                    parts.extend(self._gemini_image_parts(images))
                contents.append(T.Content(role=role, parts=parts))
        return contents

    def _gemini_image_parts(self, images: list[str]) -> list:
        T = self._types
        import base64 as _b64

        parts: list = []
        for img in images:
            try:
                if img.startswith(("http://", "https://")):
                    import requests

                    data = requests.get(img, timeout=15).content
                    parts.append(
                        T.Part(
                            inline_data=T.Blob(
                                mime_type="image/png", data=_b64.b64encode(data).decode("ascii")
                            )
                        )
                    )
                else:
                    media_type, b64 = _image_payload(img)
                    parts.append(
                        T.Part(
                            inline_data=T.Blob(
                                mime_type=media_type, data=_b64.b64decode(b64)
                            )
                        )
                    )
            except Exception:
                continue
        return parts

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        images: list[str] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        T = self._types
        config = T.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        if tools:
            config.tools = self._to_gemini_tools(tools)

        resp = self._get_client().models.generate_content(
            model=model or self.model,
            contents=self._to_gemini_contents(messages, images),
            config=config,
        )
        text = ""
        tool_calls: list[ToolCall] = []
        if resp.candidates:
            # A candidate may legitimately carry no content (safety-filtered,
            # empty, or function-call-only responses on some SDK versions) —
            # treat that as an empty response instead of crashing.
            content = getattr(resp.candidates[0], "content", None)
            if content is not None:
                for part in content.parts:
                    fc = getattr(part, "function_call", None)
                    if fc is not None:
                        tool_calls.append(
                            ToolCall(id=fc.id or "", name=fc.name, arguments=dict(fc.args or {}))
                        )
                    elif getattr(part, "text", None):
                        text += part.text
        # Also read the convenience aggregate (used when candidates aren't present).
        for fc in resp.function_calls or []:
            if fc.name not in {tc.name for tc in tool_calls}:
                tool_calls.append(
                    ToolCall(
                        id=getattr(fc, "id", None) or "",
                        name=fc.name,
                        arguments=dict(fc.args or {}),
                    )
                )
        return LLMResponse(content=text, tool_calls=tool_calls, raw=resp)
