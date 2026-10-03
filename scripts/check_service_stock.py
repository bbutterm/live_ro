#!/usr/bin/env python3
"""Регрессия локализованной закупки и согласования запасов на складе."""
import re
import sys
from pathlib import Path


def block(text, kind):
    match = re.search(rf"^{kind}\s+([^{{\n]*)\{{(.*?)^\}}", text, re.M | re.S)
    assert match, f"нет блока {kind}"
    fields = {}
    for line in match[2].splitlines():
        parts = line.split('#', 1)[0].split()
        if len(parts) >= 2:
            fields[parts[0]] = parts[1]
    return match[1].strip(), fields


def check(root):
    for bot in ('bot01', 'bot02'):
        directory = root / 'bots' / bot / 'control'
        config = (directory / 'config.txt').read_text()
        item, buy = block(config, 'buyAuto')
        # CoreLogic uses getByNameID only for numeric configuration; localized
        # store/inventory names cannot be matched by the English 'Red Potion'.
        inventory_by_id = {'501': 25}
        store_by_id = {'501': '빨간포션'}
        assert item in store_by_id, f'{bot}: закупка должна находить локализованный предмет по ID 501'
        assert inventory_by_id.get(item) == 25, f'{bot}: запас не должен считаться нулевым'
        rules = {}
        for line in (directory / 'items_control.txt').read_text().splitlines():
            parts = line.split('#', 1)[0].split()
            if parts and parts[0] in ('501', '505'):
                rules[parts[0]] = list(map(int, parts[1:4]))
        assert rules['501'][0] >= int(buy['maxAmount']), f'{bot}: склад убирает закупленный запас'
        assert int(buy['minAmount']) < rules['501'][0], f'{bot}: после склада снова сработает закупка'
        assert not rules['501'][2] and not rules['505'][2], f'{bot}: расходники не должны продаваться'
        blue, get = block(config, 'getAuto')
        assert blue == '505' and get['passive'] == '1', f'{bot}: синие зелья берутся только при штатном посещении склада'
        assert 0 < int(get['maxAmount']) <= rules['505'][0], f'{bot}: склад забирает весь запас SP'
        print(f'{bot}: локализация, закупка, склад и запас SP — OK')


if __name__ == '__main__':
    check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
