"""Save an auditable EUR retail estimate; no Azure login or billable API required."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def query(expression):
    url = 'https://prices.azure.com/api/retail/prices?' + urlencode({
        'currencyCode': 'EUR', '$filter': expression})
    rows = []
    while url:
        with urlopen(url, timeout=60) as response:
            page = json.load(response)
        rows.extend(page['Items'])
        url = page.get('NextPageLink')
    return rows


def compute_price(region, sku):
    rows = query(f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' and armSkuName eq '{sku}' and priceType eq 'Consumption'")
    eligible = [r for r in rows if r['productName'].startswith('Virtual Machines ') and 'Windows' not in r['productName'] and
                not any(s in r['meterName'] for s in ('Spot', 'Low Priority')) and r['unitOfMeasure'] == '1 Hour']
    if not eligible:
        raise ValueError(f'no regular Linux retail price for {region}/{sku}')
    # Conservatively use the highest matching primary retail meter, not a spot discount.
    return max(eligible, key=lambda r: r['retailPrice'])


def estimate(region='westeurope', workers=20, hours=4):
    if workers not in (4, 20, 40) or not 0 < hours <= 24:
        raise ValueError('unsupported pilot size or duration')
    pools = [(4, 'Standard_D4as_v5'), ((workers+2)//3, 'Standard_D8as_v5')]
    meters = [dict(count=count, meter=compute_price(region, sku)) for count, sku in pools]
    compute = sum(r['count']*r['meter']['retailPrice']*hours for r in meters)
    # Explicit allowance, NOT an asserted quote for these ancillary resources.
    # Covers 8-GiB PVCs, OS disks, LB/IP, Basic ACR, Logic App, small data transfer.
    allowance = 10.0 if hours <= 4 else 20.0
    return dict(schema='dawl.azure-estimate/1', fetched_at=datetime.now(timezone.utc).isoformat(),
                region=region, currency='EUR', workers=workers, hours=hours,
                vcpus=4*4+((workers+2)//3)*8, node_count=sum(c for c, _ in pools), meters=meters,
                compute_eur=round(compute, 2), ancillary_allowance_eur=allowance,
                estimated_envelope_eur=round((compute*1.25+allowance)*1.25, 2),
                assumptions=['Linux PAYG; no Spot or reservation', 'AKS Free control-plane tier',
                    '25% compute contingency plus explicit ancillary allowance',
                    'Additional 25% tax allowance; actual tax/discount depends on subscription; not a hard billing cap',
                    'Four-hour window includes provisioning; delete pilot resource group after exporting results'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--region', default='westeurope')
    p.add_argument('--workers', type=int, choices=[4, 20, 40], default=20)
    p.add_argument('--hours', type=float, default=4)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = estimate(a.region, a.workers, a.hours)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'meters'}, indent=2))
