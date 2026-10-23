// Keep application navigation in one document. Only the latest requested route may commit.
export function createAppNavigation({ appPath, location, history, prepare, transition, busy = () => {} }) {
  let committed = new URL(location()).href, pending = null, task = null, sequence = 0, epoch = 0;
  function route(path) {
    const url = new URL(path, location());
    const current = new URL(location());
    const pathname = url.pathname.replace(/\/$/u, '');
    return url.origin === current.origin && [appPath, `${appPath}/participants`, `${appPath}/debate`].includes(pathname) ? url : null;
  }
  async function drain(runEpoch) {
    busy(true);
    try {
      const allowed = await prepare();
      if (runEpoch !== epoch) return false;
      if (!allowed) {
        if (pending?.pop) history.replaceState(history.state, '', committed);
        pending = null;
        return false;
      }
      let applied = false;
      while (pending && runEpoch === epoch) {
        const target = pending;
        pending = null;
        const isCurrent = () => target.sequence === sequence && runEpoch === epoch;
        let didCommit = false;
        applied = await transition(target.url, {
          isCurrent,
          commit: () => {
            if (!isCurrent()) return false;
            if (didCommit) return true;
            if (!target.pop) history[target.replace ? 'replaceState' : 'pushState'](history.state, '', target.url.href);
            committed = target.url.href;
            didCommit = true;
            return true;
          },
        }) || didCommit;
      }
      return applied;
    } finally { if (runEpoch === epoch) busy(false); }
  }
  function navigate(path, { pop = false, replace = false, force = false } = {}) {
    const url = route(path);
    if (!url) return null;
    if (!task && !pop) committed = new URL(location()).href;
    const sameRoute = url.href === committed && url.href === new URL(location()).href;
    if (!task && sameRoute && !force) return Promise.resolve(true);
    pending = { url, pop, replace: replace || (force && sameRoute), sequence: ++sequence };
    if (!task) {
      const active = drain(epoch);
      task = active;
      void active.finally(() => { if (task === active) task = null; }).catch(() => {});
    }
    return task;
  }
  return {
    navigate,
    sync: () => { if (!task) committed = new URL(location()).href; },
    cancel: () => { epoch += 1; sequence += 1; pending = null; task = null; busy(false); },
  };
}

export function createViewPositions(limit = 80) {
  const positions = new Map();
  return {
    save: (key, value) => {
      if (!key || !value) return;
      positions.delete(key);
      positions.set(key, { ...value });
      while (positions.size > limit) positions.delete(positions.keys().next().value);
    },
    get: key => positions.has(key) ? { ...positions.get(key) } : null,
    clear: () => positions.clear(),
  };
}
