"""Validate v7 module alarm bytes and synchronize Monitor and Markdown copies."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.status import (
    AUTO_RESET_FAULT_MASKS, PLC_ALARM_MODULES, PLC_MODULE_WORD_OFFSETS,
    PLC_MODULE_CODE_BASES, PtmFaultMask, VisionFaultMask, plc_module_alarm_code,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check without writing files')
    args = parser.parse_args()
    source = ROOT / 'web/assets/js/plc_map_data.js'
    text = source.read_text(encoding='utf-8')
    mapping = json.loads(text[text.index('{'):text.rindex('}') + 1])
    sheets = {sheet['key']: sheet for sheet in mapping['sheets']}
    rows = sheets['03_Device_Alarm_Detail']['rows']
    seen = set()
    policy_changes = 0
    named_faults = {
        ('ptm', 'PTM_COMM_LOST'): PtmFaultMask.COMMUNICATION_LOST,
        ('ptm', 'MOTOR_DRIVER_FAULT'): PtmFaultMask.DRIVER_FAULT,
        ('ptm', 'PTM_COMM_TIMEOUT'): PtmFaultMask.COMMUNICATION_TIMEOUT,
        ('vision', 'REQUIRED_CAMERA_UNAVAILABLE'): VisionFaultMask.REQUIRED_CAMERA_UNAVAILABLE,
        ('vision', 'VISION_PIPELINE_STOPPED'): VisionFaultMask.PIPELINE_STOPPED,
        ('vision', 'VISION_RESULT_TIMEOUT'): VisionFaultMask.RESULT_TIMEOUT,
        ('vision', 'VISION_COMM_TIMEOUT'): VisionFaultMask.COMMUNICATION_TIMEOUT,
        ('vision', 'VISION_STATUS_STALE'): VisionFaultMask.STATUS_STALE,
        ('vision', 'VISION_COMM_LOST'): VisionFaultMask.COMMUNICATION_LOST,
    }
    for row in rows:
        module, severity, code = str(row[0]), str(row[1]), int(row[3])
        if severity not in {'Fault', 'Warning'} or module not in PLC_ALARM_MODULES or not 1 <= code <= 255:
            raise ValueError(f'Invalid alarm definition: {row}')
        device = next((d for d in PLC_ALARM_MODULES[module]
                       if PLC_MODULE_CODE_BASES[d] < code <= PLC_MODULE_CODE_BASES[d] + 16), None)
        if device is None:
            raise ValueError(f'Unassigned module byte: {row}')
        detail = code - PLC_MODULE_CODE_BASES[device]
        word = code << 8 if severity == 'Fault' else code
        key = (module, severity, code)
        if key in seen or int(row[7], 16) != word or row[2] != f'D+{PLC_MODULE_WORD_OFFSETS[module]}':
            raise ValueError(f'Duplicate or inconsistent alarm definition: {row}')
        seen.add(key)
        if plc_module_alarm_code(device, detail - 1) != code:
            raise ValueError(f'Byte encoder differs from codebook: {row}')
        mask = named_faults.get((module, row[4])) if severity == 'Fault' else None
        if mask is not None and mask != 1 << (detail - 1):
            raise ValueError(f'Named fault differs from codebook: {row[4]}')
        automatic = severity == 'Warning' or bool(AUTO_RESET_FAULT_MASKS.get(device, 0) & (1 << (detail - 1)))
        policy = 'Auto' if automatic else 'Latch'
        if row[6] != policy:
            if args.check:
                raise ValueError(f'Clear policy differs from code: {row[4]} ({row[7]})')
            row[6] = policy
            policy_changes += 1
    module_order = {module: index for index, module in enumerate(PLC_ALARM_MODULES)}
    rows.sort(key=lambda row: (module_order[row[0]], row[1] != 'Fault', int(row[3])))
    output = sheets['02_Controller_to_PLC']['rows']
    for offset in (20, 26, *range(50, 80)):
        matches = [row for row in output if row[1] == f'D+{offset}']
        if len(matches) != 1 or matches[0][3] != 'Reserved' or str(matches[0][9]) != '0':
            raise ValueError(f'Reserved alarm word is inconsistent: D+{offset}')

    targets = {source: 'window.TRACK_EYE_PLC_MAP = ' + json.dumps(mapping, ensure_ascii=False, indent=2) + ';\n'}
    targets[ROOT.parent / 'Traker_Monitor/datas/plc_map.json'] = json.dumps(mapping, ensure_ascii=False, indent=2) + '\n'
    guide = ROOT / 'docs/PLC_ALARM_CODES_V7.md'
    prose = guide.read_text(encoding='utf-8').split('## 상세 코드 목록', 1)[0]
    table = '## 상세 코드 목록\n\n| 모듈 | 등급 | WORD | BYTE 코드 | 원인 장치 | 단독 WORD | 항목 | 해제 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n'
    for row in rows:
        table += '| ' + ' | '.join(str(row[index]).replace('|', '/') for index in (0, 1, 2, 3, 5, 7, 4, 6)) + ' |\n'
    targets[guide] = prose + table
    for path, content in targets.items():
        if args.check:
            if path.read_text(encoding='utf-8') != content:
                raise ValueError(f'Generated map or code table is out of date: {path}')
        else:
            path.write_text(content, encoding='utf-8')
    print(f'{len(rows)} alarm definitions verified; clear policies updated: {policy_changes}')


if __name__ == '__main__':
    main()
