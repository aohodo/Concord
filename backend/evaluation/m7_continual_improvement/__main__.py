from pathlib import Path

from .catalog import build_scenarios
from .runner import dump_catalog, evaluate_scenarios, write_results


def main() -> None:
    root = Path(__file__).resolve().parent
    scenarios = build_scenarios()
    dump_catalog(root / "data" / "m7_scenarios.json", scenarios)
    runs = evaluate_scenarios(scenarios)
    output = root / "results" / "latest"
    write_results(output, runs)
    print(f"M7 scenarios={len(scenarios)} variant_runs={len(runs)} output={output}")


if __name__ == "__main__":
    main()
