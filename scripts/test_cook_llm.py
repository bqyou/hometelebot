"""
Manual test script for Cook app LLM features.

Tests:
  1. parse_recipe_from_text  -- import a pasted recipe
  2. generate_recipe         -- generate using kitchen inventory

Run:
  python scripts/test_cook_llm.py

Requires DEEPSEEK_API_KEY to be set in .env
"""

import asyncio
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.cook.llm import parse_recipe_from_text, generate_recipe, is_ai_enabled

# ============================================================
# Test data
# ============================================================

# Simulated kitchen inventory (what the "user" has at home)
TEST_INVENTORY = """Ingredients: chicken thighs (600 g), eggs (6 pcs), garlic (3 pcs), ginger (50 g), jasmine rice (2 kg), spring onion (1 bunch)
Sauces: Light soy sauce, Dark soy sauce, Oyster sauce, Sesame oil, Sambal"""

# A real recipe pasted as raw text (what user would paste into "Import Recipe")
IMPORT_TEXT = """
Garlic Butter Egg Fried Rice

Serves 2 | 15 mins

Ingredients:
- 2 cups cooked jasmine rice (day-old works best)
- 3 eggs
- 4 cloves garlic, minced
- 2 tbsp light soy sauce
- 1 tbsp oyster sauce
- 1 tbsp sesame oil
- 2 spring onions, sliced
- Salt and pepper to taste

Steps:
1. Heat wok over high heat and add oil.
2. Add minced garlic and stir-fry for 30 seconds until fragrant.
3. Push garlic to side, crack in eggs and scramble until just set.
4. Add cold rice, breaking up any clumps. Stir-fry for 2 minutes.
5. Season with soy sauce, oyster sauce and sesame oil. Toss well.
6. Garnish with spring onions and serve hot.
"""

TEST_USER_ID = 9999  # Fake user ID (won't hit real rate limit meaningfully)


# ============================================================
# Helpers
# ============================================================

def print_section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def print_recipe(data: dict):
    print(f"  Name       : {data.get('name')}")
    print(f"  Cuisine    : {data.get('cuisine')}")
    print(f"  Servings   : {data.get('servings')}")
    print(f"  Cook time  : {data.get('cook_time_minutes')} min")
    print()
    print("  Ingredients:")
    for ing in data.get("ingredients", []):
        qty = ing.get("quantity")
        unit = ing.get("unit") or ""
        name = ing.get("name")
        sauce_tag = " [sauce]" if ing.get("is_sauce") else ""
        qty_str = f"{qty}{unit} " if qty else ""
        print(f"    - {qty_str}{name}{sauce_tag}")
    print()
    print("  Equipment:", ", ".join(data.get("equipment", [])) or "(none)")
    print()
    print("  Steps:")
    for i, step in enumerate(data.get("steps", []), 1):
        print(f"    {i}. {step}")


# ============================================================
# Tests
# ============================================================

async def test_import():
    print_section("TEST 1: Import Recipe from Text")
    print("Input text:")
    print(IMPORT_TEXT.strip())
    print("\nCalling DeepSeek...\n")

    result = await parse_recipe_from_text(TEST_USER_ID, IMPORT_TEXT)

    if result is None:
        print("FAIL: Got None (API error or key missing)")
        return False
    if result.get("error") == "rate_limited":
        print("SKIP: Rate limited")
        return False

    print("PASS: Parsed successfully\n")
    print_recipe(result)
    return True


async def test_generate():
    print_section("TEST 2: Generate Recipe")
    print("Preferences:")
    print("  Cuisine    : Chinese")
    print("  Servings   : 2")
    print("  Max time   : 30 min")
    print("  Spicy      : No")
    print("  Dietary    : no pork")
    print()
    print("Kitchen inventory:")
    print(f"  {TEST_INVENTORY}")
    print("\nCalling DeepSeek...\n")

    result = await generate_recipe(
        user_id=TEST_USER_ID,
        cuisine="Chinese",
        servings=2,
        time_minutes=30,
        spicy=False,
        dietary="no pork",
        inventory_text=TEST_INVENTORY,
    )

    if result is None:
        print("FAIL: Got None (API error or key missing)")
        return False
    if result.get("error") == "rate_limited":
        print("SKIP: Rate limited")
        return False

    print("PASS: Generated successfully\n")
    print_recipe(result)
    return True


async def main():
    if not is_ai_enabled():
        print("ERROR: DEEPSEEK_API_KEY is not set in .env")
        print("Set it and re-run: DEEPSEEK_API_KEY=sk-...")
        sys.exit(1)

    print("Cook App LLM Test")
    print("DeepSeek API key: configured")

    r1 = await test_import()
    r2 = await test_generate()

    print_section("Summary")
    print(f"  Import recipe : {'PASS' if r1 else 'FAIL'}")
    print(f"  Generate recipe: {'PASS' if r2 else 'FAIL'}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
