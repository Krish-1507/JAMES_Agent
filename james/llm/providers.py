"""Concrete LLM providers.

* ``OpenAICompatibleProvider`` covers OpenAI, OpenRouter, Groq and any
  OpenAI-compatible ``custom`` endpoint (Ollama, LM Studio, vLLM, Together...).
* ``AnthropicProvider`` and ``GeminiProvider`` use their native SDKs.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .base import LLMProvider, LLMResponse, Message, Tool, ToolCall

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
        base_url: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        timeout: int = 120,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_headers = extra_headers or {}
        self._client = OpenAI(api_key=api_key or "sk-noauth", base_url=base_url, timeout=timeout)

    def validate(self) -> None:
        if not getattr(self._client, "api_key", "") or self._client.api_key == "sk-noauth":
            raise RuntimeError("Missing API key for this OpenAI-compatible provider.")

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        tool_choice: str = "auto",
        images: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        effective_model = model or self.model
        kwargs: Dict[str, Any] = dict(
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
        choice = resp.choices[0]
        message = choice.message

        tool_calls: List[ToolCall] = []
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
    def _to_anthropic_tools(tools: List[Tool]) -> List[Dict[str, Any]]:
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
    def _split_system(messages: List[Message]):
        system = None
        cleaned: List[Message] = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
                continue
            cleaned.append(m)
        return system, cleaned

    def _to_anthropic_messages(self, messages: List[Message]):
        out: List[Dict[str, Any]] = []
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
                blocks: List[Dict[str, Any]] = []
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
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        tool_choice: str = "auto",
        images: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        system, conv = self._split_system(messages)
        anthropic_messages = self._to_anthropic_messages(conv)
        kwargs: Dict[str, Any] = dict(
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
        tool_calls: List[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return LLMResponse(content=content_text, tool_calls=tool_calls, raw=resp, finish_reason=resp.stop_reason)


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
        import google.generativeai as genai

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model = genai.GenerativeModel(model)

    def validate(self) -> None:
        if not self._genai.api_key:
            raise RuntimeError("Missing GEMINI_API_KEY.")

    @staticmethod
    def _to_gemini_tools(tools: List[Tool]) -> List[Dict[str, Any]]:
        decls = []
        for t in tools:
            fn = t.get("function", t)
            decls.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return [{"function_declarations": decls}]

    def _to_gemini_contents(self, messages: List[Message]):
        mapping = {"user": "user", "assistant": "model", "tool": "user", "system": "user"}
        contents = []
        for m in messages:
            role = mapping.get(m["role"], "user")
            if m["role"] == "tool":
                contents.append({"role": "user", "parts": [f"[tool result] {m['content']}"]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                parts = []
                if m.get("content"):
                    parts.append(m["content"])
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parts.append({"function_call": {"name": fn.get("name"), "args": args}})
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append({"role": role, "parts": [m.get("content", "")]})
        return contents

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        tool_choice: str = "auto",
        images: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        contents = self._to_gemini_contents(messages)
        kwargs: Dict[str, Any] = dict(
            generation_config={
                "temperature": self.temperature,
                "max_output_tokens": self.max_tokens,
            }
        )
        if tools:
            kwargs["tools"] = self._to_gemini_tools(tools)

        resp = self._model.generate_content(contents, **kwargs)
        text = ""
        tool_calls: List[ToolCall] = []
        for part in resp.candidates[0].content.parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                tool_calls.append(ToolCall(id="", name=fc.name, arguments=dict(fc.args)))
            elif getattr(part, "text", None):
                text += part.text
        return LLMResponse(content=text, tool_calls=tool_calls, raw=resp)
