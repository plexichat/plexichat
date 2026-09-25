/**
 * Evaluate a batch of formulas in the PlexiSum *client* engine, for the
 * cross-engine differential test in test_sum_cross_engine.py.
 *
 * The two engines live in separate repositories, so the Python test cannot
 * import this one; it runs this probe under node instead. The probe reads
 * {"table": "<path to the shared case table>", "formulas": [...]} on stdin and
 * writes {"results": [{"kind", "value"}, ...]} on stdout, in the same order.
 * The table's grid is the cell fixture, so both engines read the same cells
 * and only their semantics are under test.
 *
 * A request carrying {"functions": true} instead answers {"functions": [...]},
 * the names the client engine registers, which the function-coverage test
 * holds against the shared table and the documented server-side gap.
 *
 * Usage: node cross_engine_probe.mjs <table path> <engine index.js path>
 */

import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

async function readStdin() {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

/** Turn "A5" into a 0-based [row, column], as the parity test does. */
function parseRef(ref) {
    const letters = ref.replace(/[^A-Za-z]/g, '').toUpperCase();
    const digits = ref.replace(/[^0-9]/g, '');
    let column = 0;
    for (const char of letters) column = column * 26 + (char.charCodeAt(0) - 64);
    return [Number(digits) - 1, column - 1];
}

/** Encode a result the way the Python side compares it. */
function typed(value) {
    if (typeof value === 'boolean') return { kind: 'boolean', value };
    if (typeof value === 'number') {
        // A NaN or an infinity is not a spreadsheet value, and JSON cannot
        // carry one (it stringifies to null, which the Python side read as a
        // missing number and crashed on instead of reporting). Give it its own
        // kind so the disagreement is named.
        if (!Number.isFinite(value)) return { kind: 'nonfinite', value: String(value) };
        return { kind: 'number', value };
    }
    if (value === null || value === undefined) return { kind: 'blank' };
    if (typeof value === 'string' && value.startsWith('#')) {
        return { kind: 'error', value };
    }
    return { kind: 'text', value };
}

const [, , tablePath, enginePath] = process.argv;
const request = await readStdin();
const table = JSON.parse(readFileSync(tablePath, 'utf8'));
const { createFormulaEngine } = await import(pathToFileURL(enginePath).href);

if (request.functions) {
    const engine = createFormulaEngine();
    // Written in one call and *without* process.exit, which can truncate a
    // pipe write before it flushes.
    process.stdout.write(
        JSON.stringify({ functions: [...engine.functionRegistry.keys()].sort() }),
    );
} else {
    const cells = new Map();
    for (const [ref, entry] of Object.entries(table.grid)) {
        const [row, column] = parseRef(ref);
        const cell = entry && typeof entry === 'object'
            ? { value_type: 'formula', value: null, formula: entry.formula }
            : { value_type: typeof entry, value: entry, formula: null };
        cells.set(`${row},${column}`, cell);
    }

    const resolver = (_sheet, row, column) => cells.get(`${row},${column}`) ?? null;
    const results = [];
    for (const formula of request.formulas) {
        try {
            results.push(typed(createFormulaEngine().evaluate(formula, resolver)));
        } catch (error) {
            // A thrown error is a crash, not an Excel error value: report it as
            // its own kind so the Python side never reads it as agreement.
            results.push({ kind: 'throw', value: String(error && error.message) });
        }
    }
    process.stdout.write(JSON.stringify({ results }));
}
