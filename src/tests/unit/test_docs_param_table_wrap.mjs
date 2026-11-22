/**
 * API reference parameter-table wrapping guard.
 *
 * The API reference pages under docs/api/*.md render "Field | Type |
 * Required | Description" tables through the shared docs theme. Their
 * descriptions contain long inline-code tokens (route paths, fully-qualified
 * flag names, URLs). With no overflow-wrap, the longest unbreakable token
 * becomes the column's min-content width, so the table grows past the 940px
 * content column and the reader must scroll horizontally to finish a sentence.
 * With no vertical-align, the short cells of a wrapped row sit in the middle
 * and no longer line up with the description they belong to.
 *
 * This reads the generated stylesheet source as text and asserts the rule and
 * its declarations exist. It never builds a DOM (no happy-dom deep-diff).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(
    new URL('../../api/routes/docs/theme.py', import.meta.url),
    'utf8',
);

/** Index of the `}` matching the `{` at `open`. */
function blockEnd(text, open) {
    let depth = 0;
    for (let i = open; i < text.length; i += 1) {
        if (text[i] === '{') depth += 1;
        else if (text[i] === '}') {
            depth -= 1;
            if (depth === 0) return i;
        }
    }
    return text.length - 1;
}

/** Declaration block for an exact selector prelude. */
function ruleFor(css, selector) {
    const at = css.indexOf(selector);
    assert.notEqual(at, -1, `${selector} not found in docs theme`);
    const open = css.indexOf('{', at + selector.length);
    assert.notEqual(open, -1, `no declaration block for ${selector}`);
    return css.slice(open + 1, blockEnd(css, open));
}

const cellRule = ruleFor(source, 'th, td');

test('docs tables live in a horizontally scrollable wrapper', () => {
    assert.match(ruleFor(source, '.table-wrapper'), /overflow-x:\s*auto\s*;/);
});

test('parameter-table cells wrap long descriptions', () => {
    assert.match(
        cellRule,
        /overflow-wrap:\s*anywhere\s*;/,
        'th, td must declare "overflow-wrap: anywhere" so an unbreakable ' +
        'inline-code token cannot widen the column and overflow the table',
    );
});

test('parameter-table cells align to the top when a description wraps', () => {
    assert.match(
        cellRule,
        /vertical-align:\s*top\s*;/,
        'th, td must declare "vertical-align: top" so short cells stay on ' +
        'the first line of a multi-line parameter row',
    );
});
