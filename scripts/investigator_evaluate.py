"""Run the frozen draft diagnostic set with the pinned CPU evaluator."""
import argparse
import json
from pathlib import Path
import time

from dawl_evidence.evaluate import evaluate
from dawl_evidence.index import file_hash
from dawl_evidence.investigator import NliEvaluator, POLICY, POLICY_ID
from dawl_node.common import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--model-pin', required=True)
    p.add_argument('--fixtures', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError('Evaluation output already exists')
    began = time.monotonic()
    evaluator = NliEvaluator(a.model, a.model_pin)
    ready = time.monotonic()
    result = evaluate(json.loads(a.fixtures.read_bytes()), evaluator)
    result.update(evaluator=evaluator.descriptor, evaluator_sha256=evaluator.identity,
                  policy=POLICY, policy_sha256=POLICY_ID, fixtures_sha256=file_hash(a.fixtures),
                  load_seconds=ready-began, evaluation_seconds=time.monotonic()-ready)
    write_json(a.output, result)
    print(json.dumps({k: result[k] for k in ('test', 'test_by_language', 'calibration_experiment',
                                           'evaluator_sha256', 'load_seconds', 'evaluation_seconds')}))


if __name__ == '__main__':
    main()
