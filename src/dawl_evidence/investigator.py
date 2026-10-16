"""A pinned NLI Investigator: semantic judgments remain separate from provenance."""
from datetime import datetime, timezone
import importlib.metadata
import math
from pathlib import Path
import time

from dawl_node.common import canonical_json, parse_json, sha256, sign
from .consumer import verify_collection
from .index import file_hash

POLICY = {
    'schema': 'hypertrix.investigator-policy/1',
    'method': 'multilingual-nli-passage-pairs/1',
    'support_threshold': 0.90, 'contradiction_threshold': 0.90, 'minimum_margin': 0.20,
    'max_pair_tokens': 512, 'max_pairs': 96,
    'aggregation': 'support-and-contradiction=>conflicting; support=>supported; contradiction=>contradicted; else=>insufficient_evidence',
    'oversize': 'abstain_without_truncation',
    'confidence': 'absent_until_independently_calibrated',
    'scope': 'Textual support in supplied passages; source truth, training exposure and influence are separate.',
}
POLICY_ID = sha256(canonical_json(POLICY))
LABELS = ('entailment', 'neutral', 'contradiction')


def label_scores(scores):
    if set(scores) != set(LABELS) or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        raise ValueError('Invalid NLI scores')
    if abs(sum(scores.values()) - 1) > 0.001:
        raise ValueError('NLI scores do not sum to one')
    e, n, c = (scores[k] for k in LABELS)
    if e >= POLICY['support_threshold'] and e - max(n, c) >= POLICY['minimum_margin']:
        return 'supported'
    if c >= POLICY['contradiction_threshold'] and c - max(n, e) >= POLICY['minimum_margin']:
        return 'contradicted'
    return 'insufficient_evidence'


def aggregate(labels):
    support, contradiction = 'supported' in labels, 'contradicted' in labels
    return ('conflicting' if support and contradiction else 'supported' if support else
            'contradicted' if contradiction else 'insufficient_evidence')


def result_label(result):
    if type(result.get('tokens')) is not int or result['tokens'] < 1:
        raise ValueError('Invalid evaluator token count')
    if result.get('status') == 'scored' and result['tokens'] <= POLICY['max_pair_tokens']:
        return label_scores(result['scores'])
    if (result.get('status') == 'context_limit' and result['tokens'] > POLICY['max_pair_tokens']
            and result.get('scores') is None):
        return 'insufficient_evidence'
    raise ValueError('Invalid evaluator result')


class NliEvaluator:
    def __init__(self, root, expected, threads=2):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.root = Path(root)
        if file_hash(self.root / 'manifest.json') != expected:
            raise ValueError('Evaluator manifest pin differs')
        manifest = parse_json((self.root / 'manifest.json').read_bytes())
        if manifest.get('schema') != 'hypertrix.investigator-model/1':
            raise ValueError('Unsupported evaluator manifest')
        required = {'config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'added_tokens.json', 'spm.model', 'README.md'}
        if set(manifest['files']) != required:
            raise ValueError('Unexpected evaluator files')
        for name, entry in manifest['files'].items():
            path = self.root / name
            if path.is_symlink() or path.stat().st_size != entry['bytes'] or file_hash(path) != entry['sha256']:
                raise ValueError('Evaluator artifact differs: ' + name)
        torch.set_num_threads(threads)
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.root), local_files_only=True, trust_remote_code=False, use_fast=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(self.root), local_files_only=True,
                         trust_remote_code=False, use_safetensors=True, dtype=torch.float32).eval().to('cpu')
        mapping = {int(k): v.lower() for k, v in self.model.config.id2label.items()}
        if set(mapping.values()) != set(LABELS) or set(mapping) != {0, 1, 2}:
            raise ValueError('Unknown evaluator class mapping')
        self.mapping = mapping
        self.descriptor = {'schema': 'hypertrix.investigator-evaluator/1', 'model_manifest_sha256': expected,
                           'repository': manifest['repository'], 'revision': manifest['revision'],
                           'runtime': {k: importlib.metadata.version(k) for k in ('torch', 'transformers', 'tokenizers')},
                           'device': 'cpu', 'precision': 'float32', 'threads': threads,
                           'implementation_sha256': file_hash(Path(__file__)), 'policy_sha256': POLICY_ID}
        self.identity = sha256(canonical_json(self.descriptor))

    def score(self, premise, claim):
        import torch
        encoded = self.tokenizer(premise, claim, truncation=False, return_tensors='pt')
        length = int(encoded['input_ids'].shape[1])
        if length > POLICY['max_pair_tokens']:
            return {'status': 'context_limit', 'tokens': length, 'scores': None}
        with torch.inference_mode():
            logits = self.model(**encoded).logits[0].float()
            values = logits.softmax(-1).tolist()
        scores = {self.mapping[i]: float(v) for i, v in enumerate(values)}
        label_scores(scores)  # Validate numerical results before recording them.
        return {'status': 'scored', 'tokens': length, 'scores': scores}


def assess_claim(claim, candidates, evaluator):
    judgments = []
    for candidate in candidates:
        result = evaluator.score(candidate['text'], claim)
        label = result_label(result)
        explanation = {
            'supported': 'The evaluator classifies this passage as entailing the claim under the recorded policy.',
            'contradicted': 'The evaluator classifies this passage as contradicting the claim under the recorded policy.',
            'insufficient_evidence': 'This passage does not meet the recorded support or contradiction threshold.',
        }[label] if result['status'] == 'scored' else 'The complete passage and claim exceed the evaluator context; no text was discarded.'
        judgments.append({'passage_sha256': candidate['passage_sha256'], 'source_sha256': candidate['source_sha256'],
                          'start': candidate['start'], 'end': candidate['end'], 'source_group': candidate.get('source_group'),
                          'verdict': label, 'explanation': explanation, 'raw_nli': result,
                          'confidence': None, 'calibration': None})
    verdict = aggregate([j['verdict'] for j in judgments])
    return {'verdict': verdict, 'judgments': judgments,
            'supporting_passages': [j['passage_sha256'] for j in judgments if j['verdict'] == 'supported'],
            'contradicting_passages': [j['passage_sha256'] for j in judgments if j['verdict'] == 'contradicted'],
            'source_groups': sorted({j['source_group'] for j in judgments if j['source_group']}),
            'confidence': None, 'calibration': None}


def investigate(collection, consumer, blueprint, answer, fetch, evaluator, key, nonce):
    from data_ingestion.protocol import require_digest
    require_digest(nonce)
    began = time.monotonic()
    verified = verify_collection(collection, consumer, blueprint, answer, fetch)
    original = collection['body']
    count = sum(len(c['candidates']) for c in original['claims'])
    if count > POLICY['max_pairs']:
        raise ValueError('Assessment exceeds pair budget')
    results = []
    for claim in original['claims']:
        results.append({'start': claim['start'], 'end': claim['end'], 'text': claim['text'],
                        **assess_claim(claim['text'], claim['candidates'], evaluator)})
    return sign({'schema': 'hypertrix.investigation/1', 'collection': collection['id'],
                 'nonce': nonce, 'created_at': datetime.now(timezone.utc).isoformat(),
                 'answer_sha256': original['answer_sha256'], 'checkpoint': original['checkpoint'],
                 'checkpoint_binding': original['checkpoint_binding'], 'blueprint': blueprint.identity,
                 'consumer': consumer, 'evaluator': evaluator.descriptor, 'evaluator_sha256': evaluator.identity,
                 'policy': POLICY, 'policy_sha256': POLICY_ID, 'provenance_verification': verified,
                 'claims': results, 'collection_failures': len(original['failures']),
                 'counterevidence_scope': 'All supplied passages checked for contradiction; no additional search performed.',
                 'elapsed_seconds': time.monotonic() - began}, key)


def verify_assessment(receipt, investigator, collection, expected_evaluator, nonce):
    """Validate the signature, evidence linkage and policy math; no semantic replay."""
    from dawl_node.common import verify_signature
    body = verify_signature(receipt, investigator)
    original = verify_signature(collection, collection['body']['signer'])
    if (body.get('schema') != 'hypertrix.investigation/1' or body['collection'] != collection['id']
            or body['nonce'] != nonce or body['answer_sha256'] != original['answer_sha256']
            or body['checkpoint'] != original['checkpoint'] or body['blueprint'] != original['blueprint']
            or body['checkpoint_binding'] != original['checkpoint_binding']
            or body['consumer'] != original['signer'] or body['policy'] != POLICY or body['policy_sha256'] != POLICY_ID
            or body['evaluator_sha256'] != expected_evaluator
            or sha256(canonical_json(body['evaluator'])) != expected_evaluator
            or len(body['claims']) != len(original['claims'])):
        raise ValueError('Investigation identity or evidence binding differs')
    proof = body['provenance_verification']
    if (proof.get('verified') is not True or proof.get('collection') != collection['id']
            or body['collection_failures'] != len(original['failures'])):
        raise ValueError('Investigation provenance binding differs')
    for result, claim in zip(body['claims'], original['claims']):
        if any(result[k] != claim[k] for k in ('start', 'end', 'text')) or len(result['judgments']) != len(claim['candidates']):
            raise ValueError('Investigation claim or citation count differs')
        for judgment, candidate in zip(result['judgments'], claim['candidates']):
            if any(judgment[k] != candidate[k] for k in ('passage_sha256', 'source_sha256', 'start', 'end', 'source_group')):
                raise ValueError('Investigation citation differs')
            raw = judgment['raw_nli']
            expected = result_label(raw)
            if judgment['verdict'] != expected or judgment['confidence'] is not None or judgment['calibration'] is not None:
                raise ValueError('Unsupported assessment verdict or confidence')
        labels = [j['verdict'] for j in result['judgments']]
        if (result['verdict'] != aggregate(labels) or result['confidence'] is not None or result['calibration'] is not None
                or result['supporting_passages'] != [j['passage_sha256'] for j in result['judgments'] if j['verdict'] == 'supported']
                or result['contradicting_passages'] != [j['passage_sha256'] for j in result['judgments'] if j['verdict'] == 'contradicted']):
            raise ValueError('Investigation aggregation differs')
        if result['source_groups'] != sorted({j['source_group'] for j in result['judgments'] if j['source_group']}):
            raise ValueError('Investigation source groups differ')
    return {'verified': True, 'scope': 'Signature, bindings and policy arithmetic; semantic judgments require evaluator replay.',
            'collection': collection['id'], 'investigation': receipt['id']}
