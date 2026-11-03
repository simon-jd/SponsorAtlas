function finiteNumber(value) {
  if (typeof value !== 'number' && typeof value !== 'string') {
    throw Error('Metric must be a finite number or numeric string');
  }
  if (typeof value === 'string' && !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())) {
    throw Error('Metric string is not numeric');
  }
  const number = Number(value);
  if (!Number.isFinite(number)) throw Error('Metric must be finite');
  return number;
}

function assertNumericEqual(actual, expected, tolerance = 1e-6) {
  if (expected === null) {
    if (actual !== null) throw Error('Metric must be null');
    return;
  }
  const received = finiteNumber(actual);
  const wanted = finiteNumber(expected);
  if (Math.abs(received - wanted) > tolerance) throw Error('REST/source SQL mismatch');
}

// No tolerance: for counts and for rates computed as numerator / denominator on both sides.
function assertExactEqual(actual, expected) {
  if (finiteNumber(actual) !== finiteNumber(expected)) throw Error('REST/source SQL mismatch (exact)');
}

// A rate must equal numerator / denominator with no tolerance: either as the same double
// (DuckDB, client-side division) or as the exact ratio rounded half-up to the decimal scale
// Snowflake returned (NUMBER division has a fixed scale, at least 6). Null iff denominator is 0.
function assertExactRatio(actual, numerator, denominator) {
  const num = finiteNumber(numerator);
  const den = finiteNumber(denominator);
  if (!Number.isInteger(num) || !Number.isInteger(den) || num < 0 || den < 0) throw Error('Ratio needs nonnegative integer counts');
  if (den === 0) {
    if (actual !== null) throw Error('Rate must be null for an empty denominator');
    return;
  }
  const received = finiteNumber(actual);
  if (received === num / den) return;
  const text = typeof actual === 'string' ? actual.trim() : '';
  const match = /^(\d+)\.(\d{6,30})$/.exec(text);
  if (!match) throw Error('REST/source SQL mismatch (ratio)');
  const scale = BigInt(10) ** BigInt(match[2].length);
  const rounded = (BigInt(num) * scale * 2n + BigInt(den)) / (2n * BigInt(den));
  if (BigInt(match[1] + match[2]) !== rounded) throw Error('REST/source SQL mismatch (ratio)');
}

function assertMetric(row, field, expected) {
  if (!row || !Object.hasOwn(row, field)) throw Error('Metric field missing: ' + field);
  assertNumericEqual(row[field], expected);
}

async function stopChild(child, timeoutMs = 10000) {
  if (!child) return;
  const assertExit = (code, signal, timedOut) => {
    if (timedOut || code !== 0 || signal) throw Error('Cube child cleanup did not complete successfully');
  };
  if (child.exitCode !== null || child.signalCode !== null) {
    assertExit(child.exitCode, child.signalCode, false);
    return;
  }
  await new Promise((resolve, reject) => {
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, timeoutMs);
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      try { assertExit(code, signal, timedOut); resolve(); } catch (error) { reject(error); }
    });
    child.kill('SIGTERM');
  });
}

module.exports = {finiteNumber, assertNumericEqual, assertExactEqual, assertExactRatio, assertMetric, stopChild};
