"""
Intent classification evaluation.
Runs in CI/CD — blocks deployment if accuracy < 85%.
"""
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.intent_agent import classify_intent
from dotenv import load_dotenv

load_dotenv()

GOLDEN_SET_PATH = Path(__file__).parent / "datasets" / "golden_set.json"
ACCURACY_THRESHOLD = 0.85

def run_eval():
    print("🧪 Running intent classification eval...")

    with open(GOLDEN_SET_PATH) as f:
        golden_set = json.load(f)

    results = []
    correct = 0
    total = len(golden_set)

    for test_case in golden_set:
        inp = test_case["input"]
        expected = test_case["expected"]

        result = classify_intent(
            message=inp,
            business_name="SmileCare Dental"
        )
        predicted = result.get("intent", "UNKNOWN")
        is_correct = predicted == expected

        if is_correct:
            correct += 1

        results.append({
            "input": inp,
            "expected": expected,
            "predicted": predicted,
            "correct": is_correct,
            "confidence": result.get("confidence", 0)
        })

        status = "✅" if is_correct else "❌"
        print(f"{status} '{inp[:40]}...' → expected: {expected}, got: {predicted}")

    accuracy = correct / total
    print(f"\n{'='*50}")
    print(f"📊 Results: {correct}/{total} correct")
    print(f"🎯 Accuracy: {accuracy:.1%}")
    print(f"📈 Threshold: {ACCURACY_THRESHOLD:.0%}")
    print(f"{'='*50}")

    # Save results
    with open("evals/last_results.json", "w") as f:
        json.dump({
            "accuracy": accuracy,
            "correct": correct,
            "total": total,
            "threshold": ACCURACY_THRESHOLD,
            "passed": accuracy >= ACCURACY_THRESHOLD,
            "results": results
        }, f, indent=2)

    # Exit with error if below threshold — blocks CI/CD
    if accuracy < ACCURACY_THRESHOLD:
        print(f"\n❌ EVAL FAILED: {accuracy:.1%} < {ACCURACY_THRESHOLD:.0%}")
        print("Deployment blocked until accuracy improves.")
        sys.exit(1)
    else:
        print(f"\n✅ EVAL PASSED: {accuracy:.1%} >= {ACCURACY_THRESHOLD:.0%}")
        print("Deployment approved.")
        sys.exit(0)

if __name__ == "__main__":
    run_eval()