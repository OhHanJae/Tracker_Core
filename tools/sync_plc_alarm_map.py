"""Validate the v5.2 alarm bitmap contract and synchronize the Monitor map."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.common.status import PLC_V5_2_DETAIL_MASKS, PLC_V5_2_DETAIL_OFFSETS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check without writing files')
    args = parser.parse_args()
    source = ROOT / 'web/assets/js/plc_map_data.js'
    text = source.read_text(encoding='utf-8')
    mapping = json.loads(text[text.index('{'):text.rindex('}') + 1])
    if mapping.get('alarmFormat') != 'bitmap_v5_2':
        raise ValueError('Expected v5.2 alarm bitmap metadata')
    sheets = {sheet['key']: sheet for sheet in mapping['sheets']}
    output = sheets['02_Controller_to_PLC']['rows']
    names = {row[1]: row[3] for row in output}
    for offset, name in {21: 'PRIMARY_FAULT_CODE', 22: 'PRIMARY_WARNING_CODE',
                         23: 'LAST_FAULT_CODE', 24: 'ALARM_SEQUENCE',
                         25: 'DEVICE_FAULT_SUMMARY', 26: 'DEVICE_WARNING_SUMMARY'}.items():
        if names.get(f'D+{offset}') != name:
            raise ValueError(f'Incorrect alarm word D+{offset}')
    for offset in range(52, 58):
        if names.get(f'D+{offset}') != 'Reserved':
            raise ValueError(f'Camera reserved word is allocated: D+{offset}')
    masks = {}
    seen = set()
    for row in sheets['03_Device_Alarm_Detail']['rows']:
        if str(row[4]).lower().startswith('reserved') or row[1] not in ('Warning', 'Fault'):
            continue
        offset = int(str(row[2]).replace('D+', ''))
        if offset in (66, 67):  # GPIO-only laser has no alarm feedback.
            continue
        bit = int(str(row[3]), 16)
        key = (offset, bit)
        if key in seen or not 0 <= bit <= 15:
            raise ValueError(f'Duplicate or invalid alarm bit: {row}')
        seen.add(key)
        masks[offset] = masks.get(offset, 0) | (1 << bit)
    for device, offset in PLC_V5_2_DETAIL_OFFSETS.items():
        expected = PLC_V5_2_DETAIL_MASKS[device]
        if (masks.get(offset, 0), masks.get(offset + 1, 0)) != expected:
            raise ValueError(f'Alarm metadata masks differ for {device.name}')
    target = ROOT.parent / 'Traker_Monitor/datas/plc_map.json'
    content = json.dumps(mapping, ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if target.read_text(encoding='utf-8') != content:
            raise ValueError('Monitor PLC map is out of date')
    else:
        target.write_text(content, encoding='utf-8')
    print(f'{len(seen)} v5.2 alarm bits verified; Monitor map synchronized')


if __name__ == '__main__':
    main()
