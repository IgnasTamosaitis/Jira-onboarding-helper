const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('../.tools/typescript-check/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../power_automate/ReserveAccessCard.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText;
const runScript = vm.runInNewContext(compiled + '\nmain;', {});
const HEADERS = ['CardID', 'EmployeeName', 'JiraKey', 'Status', 'ReservedAt'];
const row = (id, name = '', jira = '', status = '', date = '') => [id, name, jira, status, date];

function workbook(records, options = {}) {
    const state = {rows: [HEADERS.slice(), ...records.map(r => r.slice())], writes: 0};
    const all = {getTexts: () => state.rows.map(r => r.slice()), getUsedRange: valuesOnly => {
        assert.equal(valuesOnly, true, 'Ignore the production sheet\'s million formatted rows');
        return all;
    }};
    const sheet = {
        getRange: address => address === 'A1:E1' ? {getTexts: () => [state.rows[0].slice()]} : all,
        getRangeByIndexes: (start, column, count, width) => {
            assert.deepEqual([column, count, width], [0, 1, 5]);
            return {
                getTexts: () => [options.changedRow || (state.rows[start] || row('')).slice()],
                setValues: values => {
                    state.writes++;
                    state.rows[start] = values[0].slice();
                    if (options.corruptWrite) state.rows[start][1] = 'Someone Else';
                },
            };
        },
    };
    state.book = {getWorksheet: name => name === 'AccessCards' ? sheet : undefined};
    state.call = (kind = 'new_joiner', name = 'Test Employee', key = 'GSD-123') =>
        JSON.parse(runScript(state.book, key, name, kind));
    return state;
}

test('claims LT5053 in the existing row and leaves old gaps untouched', () => {
    const w = workbook([row('LT869'), row('LT5001'), row('LT5052', 'Previous Employee'), row('LT5053'), row('LT5054')]);
    const result = w.call();
    assert.equal(result.status, 'reserved');
    assert.equal(result.cardId, 'LT5053');
    assert.equal(w.rows[4][1], 'Test Employee');
    assert.equal(w.rows[4][2], 'GSD-123');
    assert.equal(w.rows[1][1], '');
    assert.equal(w.rows[2][1], '');
    assert.equal(w.rows.length, 6);
    assert.equal(w.writes, 1);
});

test('repeated Jira requests do not allocate another card', () => {
    const w = workbook([row('LT5053'), row('LT5054')]);
    w.call();
    const second = w.call();
    assert.equal(second.status, 'existing');
    assert.equal(second.cardId, 'LT5053');
    assert.equal(second.workbookChanged, false);
    assert.equal(w.writes, 1);
});

test('serialized requests get consecutive unique IDs', () => {
    const w = workbook([row('LT5053'), row('LT5054')]);
    assert.equal(w.call().cardId, 'LT5053');
    assert.equal(w.call('new_joiner', 'Another Employee', 'GSD-124').cardId, 'LT5054');
    assert.equal(w.writes, 2);
});

test('rejoiner copies the historical spelling and never writes any cell', () => {
    const w = workbook([row('LT40', 'Ąžuolas Žemaitis'), row('LT5053')]);
    const before = JSON.stringify(w.rows);
    const result = w.call('rejoiner', 'Azuolas Zemaitis');
    assert.equal(result.status, 'reused');
    assert.equal(result.cardId, 'LT40');
    assert.equal(result.registryName, 'Ąžuolas Žemaitis');
    assert.equal(result.fullName, 'Azuolas Zemaitis');
    assert.equal(result.workbookChanged, false);
    assert.equal(JSON.stringify(w.rows), before);
    assert.equal(w.writes, 0);
});

test('zero or multiple rejoiner name matches require review and never allocate', () => {
    for (const records of [[row('LT1', 'Someone Else')], [row('LT1', 'Test Employee'), row('LT2', 'TEST EMPLOYEE')]]) {
        const w = workbook(records);
        assert.equal(w.call('rejoiner').status, 'manual_review');
        assert.equal(w.writes, 0);
    }
});

test('new joiner matching an old name requires review', () => {
    const w = workbook([row('LT20', 'Test Employee')]);
    assert.equal(w.call().status, 'manual_review');
    assert.equal(w.writes, 0);
});

test('range exhaustion, malformed IDs and duplicate candidate IDs block allocation', () => {
    for (const records of [[row('LT9999', 'Last Employee')], [row('LT050', 'Employee')], [row('LT5053'), row('LT5053')]]) {
        const w = workbook(records);
        assert.equal(w.call().status, 'blocked');
        assert.equal(w.writes, 0);
    }
});

test('legacy duplicate IDs stay untouched and cannot be reused or printed', () => {
    const w = workbook([row('LT1950', 'One Employee', 'GSD-120'), row('LT1950', 'Another Employee'), row('LT5052', 'Previous Employee'), row('LT5053')]);
    const legacyRows = JSON.stringify(w.rows.slice(0, 3));
    for (const [kind, name, key] of [
        ['rejoiner', 'One Employee', 'GSD-121'],
        ['rejoiner', 'Another Employee', 'GSD-122'],
        ['new_joiner', 'One Employee', 'GSD-120'],
    ]) {
        const result = w.call(kind, name, key);
        assert.equal(result.status, 'manual_review');
        assert.equal(result.cardId, null);
        assert.equal(result.workbookChanged, false);
    }
    assert.equal(w.writes, 0);
    const reserved = w.call();
    assert.equal(reserved.status, 'reserved');
    assert.equal(reserved.cardId, 'LT5053');
    assert.equal(w.writes, 1);
    assert.equal(JSON.stringify(w.rows.slice(0, 3)), legacyRows);
});

test('appends after the prefilled list is exhausted and respects the minimum', () => {
    for (const [records, expected] of [[[row('LT5099', 'Previous Employee')], 'LT5100'], [[], 'LT5053']]) {
        const w = workbook(records);
        assert.equal(w.call().cardId, expected);
        assert.equal(w.writes, 1);
    }
});

test('a changed candidate row is not overwritten', () => {
    const w = workbook([row('LT5053')], {changedRow: row('LT5053', 'Concurrent Employee')});
    assert.equal(w.call().status, 'blocked');
    assert.equal(w.writes, 0);
});

test('failed write verification never returns a printable ID', () => {
    const w = workbook([row('LT5053')], {corruptWrite: true});
    const result = w.call();
    assert.equal(result.status, 'blocked');
    assert.equal(result.cardId, null);
    assert.equal(result.workbookChanged, true);
});

test('movers, spreadsheet formulas and conflicting Jira identities never write', () => {
    const w = workbook([row('LT1', 'Someone Else', 'GSD-123')]);
    assert.equal(w.call('mover').status, 'blocked');
    assert.equal(w.call('new_joiner', '=HYPERLINK(...)').status, 'blocked');
    assert.equal(w.call().status, 'manual_review');
    assert.equal(w.writes, 0);
});
