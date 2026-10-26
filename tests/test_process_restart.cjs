const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/assets/js/app.js'), 'utf8');
const start = source.indexOf('  document.querySelector(\'[data-action="restart-all"]\')?.addEventListener');
const end = source.indexOf('\n  });', start);
assert.ok(start >= 0 && end > start, 'Restart All binding must exist');
const binding = source.slice(start, end + '\n  });'.length);

async function restartAll(modules, { busy = false, failed = [] } = {}) {
  const calls = [];
  const toasts = [];
  const state = { modules, restartAllPending: busy, processActions: {}, moduleActions: {} };
  let handler;
  vm.runInNewContext(binding, {
    document: { querySelector: () => ({ addEventListener: (_event, callback) => { handler = callback; } }) },
    state,
    isManagedModule: module => Boolean(module.process_name),
    moduleProcessName: module => module.process_name,
    isConfiguredProcessName: () => true,
    pushProcessAction: async (name, action, options) => {
      assert.equal(action, 'restart');
      assert.equal(options.batch, true);
      calls.push(name);
      return !failed.includes(name);
    },
    showToast: message => toasts.push(message),
    render: () => {},
  });
  await handler();
  return { calls, state, toasts };
}

async function main() {
  const modules = [
    { process_name: 'plc_gateway', enabled: true },
    { process_name: 'vision_server', enabled: false },
    { process_name: 'ptm_server' },
    { key: 'main', enabled: true },
  ];
  const mixed = await restartAll(modules);
  assert.deepEqual(mixed.calls, ['plc_gateway', 'ptm_server']);
  assert.equal(mixed.state.restartAllPending, false);
  assert.match(mixed.toasts.at(-1), /성공 2개/);

  const disabled = await restartAll([{ process_name: 'vision_server', enabled: false }]);
  assert.deepEqual(disabled.calls, []);
  assert.ok(!disabled.state.restartAllPending);
  assert.equal(disabled.toasts.length, 1);

  const busy = await restartAll(modules, { busy: true });
  assert.deepEqual(busy.calls, []);

  const partial = await restartAll(modules, { failed: ['plc_gateway'] });
  assert.deepEqual(partial.calls, ['plc_gateway', 'ptm_server']);
  assert.match(partial.toasts.at(-1), /성공 1개.*실패 1개/);
  assert.equal(partial.state.restartAllPending, false);
  console.log('PASS Restart All: disabled skipped; enabled/default restarted; empty/busy/failure handled');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
