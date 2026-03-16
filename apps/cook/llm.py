"""DeepSeek API integration for Cook app AI features."""

import json
import logging
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
RATE_LIMIT = 20  # requests per user per day
_usage: dict[int, list[float]] = {}  # {user_id: [timestamps]}


def is_ai_enabled() -> bool:
    return bool(settings.deepseek_api_key)


def check_rate_limit(user_id: int) -> bool:
    """Return True if user can make a request, False if limit exceeded."""
    now = time.time()
    cutoff = now - 86400
    timestamps = _usage.get(user_id, [])
    timestamps = [t for t in timestamps if t > cutoff]
    _usage[user_id] = timestamps
    return len(timestamps) < RATE_LIMIT


def _record_usage(user_id: int) -> None:
    _usage.setdefault(user_id, []).append(time.time())


async def call_deepseek(system_prompt: str, user_message: str, max_tokens: int = 2000) -> str | None:
    """Call the DeepSeek API and return the response content."""
    if not settings.deepseek_api_key:
        return None

    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("DeepSeek API call failed")
        return None


PARSE_SYSTEM_PROMPT = """You are a recipe parser. Given raw recipe text, extract structured data.
Return JSON with these fields:
{
  "name": "Recipe Name",
  "servings": 2,
  "cuisine": "Chinese" or null,
  "cook_time_minutes": 30 or null,
  "ingredients": [
    {"name": "chicken breast", "quantity": 200, "unit": "g", "is_sauce": false},
    {"name": "soy sauce", "quantity": 2, "unit": "tbsp", "is_sauce": true}
  ],
  "equipment": ["Wok", "Cutting board"],
  "steps": ["Step 1 text", "Step 2 text"]
}
For ingredients where quantity is unclear (e.g. "to taste", "some"), set quantity and unit to null.
Mark common sauces/condiments/oils/spices with is_sauce: true."""

GENERATE_SYSTEM_PROMPT = """You are a home cooking assistant for families in Singapore.
Generate a recipe based on the user's preferences and available ingredients.
Return JSON with these fields:
{
  "name": "Recipe Name",
  "servings": 2,
  "cuisine": "Chinese",
  "cook_time_minutes": 30,
  "ingredients": [
    {"name": "chicken breast", "quantity": 200, "unit": "g", "is_sauce": false},
    {"name": "soy sauce", "quantity": 2, "unit": "tbsp", "is_sauce": true}
  ],
  "equipment": ["Wok", "Cutting board"],
  "steps": ["Step 1 text", "Step 2 text"]
}
Prioritize using ingredients the user already has. Keep it practical for home cooking.
Mark common sauces/condiments/oils/spices with is_sauce: true."""


async def parse_recipe_from_text(user_id: int, raw_text: str) -> dict | None:
    """Parse raw recipe text into structured data using DeepSeek."""
    if not check_rate_limit(user_id):
        return {"error": "rate_limited"}

    _record_usage(user_id)
    result = await call_deepseek(PARSE_SYSTEM_PROMPT, raw_text)
    if not result:
        return None

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        logger.error("Failed to parse DeepSeek response as JSON")
        return None


async def generate_recipe(
    user_id: int,
    cuisine: str,
    servings: int,
    time_minutes: int,
    spicy: bool,
    dietary: str,
    inventory_text: str,
) -> dict | None:
    """Generate a recipe using DeepSeek with user preferences and inventory context."""
    if not check_rate_limit(user_id):
        return {"error": "rate_limited"}

    _record_usage(user_id)
    user_message = (
        f"Cuisine: {cuisine}\n"
        f"Servings: {servings}\n"
        f"Max cooking time: {time_minutes} minutes\n"
        f"Spicy: {'Yes' if spicy else 'No'}\n"
        f"Dietary restrictions: {dietary or 'None'}\n\n"
        f"Available ingredients and sauces:\n{inventory_text}"
    )
    result = await call_deepseek(GENERATE_SYSTEM_PROMPT, user_message)
    if not result:
        return None

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        logger.error("Failed to parse DeepSeek response as JSON")
        return None
