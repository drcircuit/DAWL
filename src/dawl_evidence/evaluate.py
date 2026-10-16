"""ALCE-inspired citation diagnostics with independent expected fixture labels.

These are not official ALCE scores. Test cases never fit thresholds/temperature.
Draft fixtures cannot authorize a production confidence estimate.
"""
import math
from collections import Counter

from .investigator import LABELS, label_scores


def rescale(scores, temperature):
    if not 0 < temperature <= 10:
        raise ValueError('Invalid temperature')
    logs = {k: math.log(max(v, 1e-12)) / temperature for k, v in scores.items()}
    ceiling = max(logs.values())
    weights = {k: math.exp(v - ceiling) for k, v in logs.items()}
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def calibration_diagnostics(rows, temperature=1):
    if not rows:
        return {'count': 0, 'brier_multiclass': None, 'entailment_ece': None, 'bins': []}
    buckets = [[] for _ in range(10)]
    brier = 0
    for row in rows:
        score = rescale(row['scores'], temperature)
        brier += sum((score[k] - int(row['gold'] == k)) ** 2 for k in LABELS)
        probability = score['entailment']
        buckets[min(9, int(probability * 10))].append((probability, int(row['gold'] == 'entailment')))
    bins, ece = [], 0
    for i, values in enumerate(buckets):
        if not values:
            continue
        predicted = sum(x for x, _ in values) / len(values)
        observed = sum(y for _, y in values) / len(values)
        ece += len(values) * abs(predicted - observed) / len(rows)
        bins.append({'lower': i / 10, 'upper': (i + 1) / 10, 'count': len(values),
                     'mean_score': predicted, 'observed_support_fraction': observed})
    return {'count': len(rows), 'brier_multiclass': brier / len(rows), 'entailment_ece': ece, 'bins': bins}


def metrics(rows):
    def ratio(n, d):
        return n / d if d else None
    confusion = Counter((r['gold'], r['predicted']) for r in rows)
    selected = [r for r in rows if r['predicted'] == 'supported']
    gold_support = [r for r in rows if r['gold'] == 'entailment']
    not_support = [r for r in rows if r['gold'] != 'entailment']
    contradictions = [r for r in rows if r['gold'] == 'contradiction']
    labels = {'entailment': 'supported', 'contradiction': 'contradicted', 'neutral': 'insufficient_evidence'}
    return {'cases': len(rows), 'selected_citations': len(selected),
            'citation_precision_against_labels': ratio(sum(r['gold'] == 'entailment' for r in selected), len(selected)),
            'supported_claim_recall': ratio(sum(r['predicted'] == 'supported' for r in gold_support), len(gold_support)),
            'false_support_rate': ratio(sum(r['predicted'] == 'supported' for r in not_support), len(not_support)),
            'contradiction_recall': ratio(sum(r['predicted'] == 'contradicted' for r in contradictions), len(contradictions)),
            'three_way_accuracy': ratio(sum(labels[r['gold']] == r['predicted'] for r in rows), len(rows)),
            'abstention_rate': ratio(sum(r['predicted'] == 'insufficient_evidence' for r in rows), len(rows)),
            'confusion': [{'gold': g, 'predicted': p, 'count': n} for (g, p), n in sorted(confusion.items())]}


def evaluate(fixtures, evaluator):
    if fixtures.get('schema') != 'hypertrix.investigator-fixtures/1':
        raise ValueError('Invalid fixture schema')
    if tuple(fixtures['label_order']) != LABELS:
        # Explicit order is part of this small dataset format, not inferred from text.
        if set(fixtures['label_order']) != set(LABELS) or len(fixtures['label_order']) != 3:
            raise ValueError('Invalid fixture labels')
    rows, groups = [], set()
    for group in fixtures['groups']:
        if group['id'] in groups or group['split'] not in ('development', 'test'):
            raise ValueError('Duplicate/leaking source family or unknown split')
        groups.add(group['id'])
        if len(group['claims']) != len(fixtures['label_order']):
            raise ValueError('Missing expected labels')
        for i, (claim, gold) in enumerate(zip(group['claims'], fixtures['label_order'])):
            result = evaluator.score(group['passage'], claim)
            if result['status'] != 'scored':
                raise ValueError('Fixture exceeds context; revise the dataset version rather than dropping it')
            rows.append({'id': group['id'] + '/' + str(i), 'group': group['id'], 'split': group['split'],
                         'language': group['language'], 'claim': claim, 'gold': gold,
                         'scores': result['scores'], 'predicted': label_scores(result['scores'])})
    development = [r for r in rows if r['split'] == 'development']
    test = [r for r in rows if r['split'] == 'test']
    if not development or not test:
        raise ValueError('Both disjoint development and test groups are required')
    candidates = [.5, .75, 1, 1.25, 1.5, 2, 3, 4]
    temperature = min(candidates, key=lambda t: sum(-math.log(max(rescale(r['scores'], t)[r['gold']], 1e-12)) for r in development))
    return {'schema': 'hypertrix.investigator-evaluation/1', 'annotation_status': fixtures['annotation_status'],
            'official_alce_run': False, 'rows': rows, 'development': metrics(development), 'test': metrics(test),
            'test_by_language': {lang: metrics([r for r in test if r['language'] == lang]) for lang in sorted({r['language'] for r in test})},
            'calibration_experiment': {'temperature_fit_split': 'development', 'temperature': temperature,
                'raw_test': calibration_diagnostics(test), 'rescaled_test': calibration_diagnostics(test, temperature),
                'production_eligible': False, 'reason': 'Small agent-authored fixtures lack independent annotation and representative coverage.'}}
