const {SnowflakeDriver} = require('@cubejs-backend/snowflake-driver');

// Keep the pinned driver's execution/hydration/cancellation implementation. The
// adapter guards the SDK execute boundary and owns connection cleanup instead.
class LifecycleSnowflakeDriver extends SnowflakeDriver {
  constructor(config, cutoff, clock = Date.now) {
    super(config);
    this.cutoff = cutoff;
    this.clock = clock;
    this.closed = false;
    this.connections = new Set();
    this.connectionWaits = new Set();
    this.waiters = new Set();
    this.destroyPromises = new WeakMap();
    this.destroyFailures = [];
    this.releasePromise = null;
  }

  guard() {
    if (this.closed) throw Error('Snowflake driver released');
    if (this.clock() >= this.cutoff) throw Error('Snowflake trial query cutoff reached');
  }

  track(connection) {
    this.connections.add(connection);
    if (this.closed) this.destroyConnection(connection).catch(() => {});
    return connection;
  }

  destroyConnection(connection) {
    if (!connection) return Promise.resolve();
    const previous = this.destroyPromises.get(connection);
    if (previous) return previous;
    const promise = new Promise((resolve, reject) => {
      try {
        connection.destroy(error => error ? reject(error) : resolve());
      } catch (error) {
        reject(error);
      }
    });
    this.destroyPromises.set(connection, promise);
    promise.then(
      () => this.connections.delete(connection),
      error => {
        this.connections.delete(connection);
        this.destroyFailures.push(error);
      },
    );
    return promise;
  }

  // SDK connect/reconnect waits are interrupted by cutoff or release. A late
  // callback cannot cause SQL dispatch; raw connections are tracked separately.
  waitFor(promise) {
    let rejectWait;
    let done = false;
    let timer;
    const cleanup = () => {
      clearTimeout(timer);
      this.waiters.delete(abort);
    };
    const abort = error => {
      if (done) return;
      done = true;
      cleanup();
      rejectWait(error);
    };
    const result = new Promise((resolve, reject) => {
      rejectWait = reject;
      Promise.resolve(promise).then(value => {
        if (done) return;
        try {
          this.guard();
          done = true;
          cleanup();
          resolve(value);
        } catch (error) {
          abort(error);
          void this.release().catch(() => {});
        }
      }, abort);
    });
    this.waiters.add(abort);
    const check = () => {
      if (done) return;
      try {
        this.guard();
        timer = setTimeout(check, Math.max(1, Math.min(1000, this.cutoff - this.clock())));
      } catch (error) {
        abort(error);
        void this.release().catch(() => {});
      }
    };
    check();
    return {promise: result, abort};
  }

  getConnection() {
    this.guard();
    if (this.connection) {
      Promise.resolve(this.connection).then(connection => this.track(connection), () => {});
    }
    return super.getConnection();
  }

  async createConnection() {
    const connection = this.track(await super.createConnection());
    try {
      this.guard();
      return connection;
    } catch (error) {
      await this.destroyConnection(connection);
      throw error;
    }
  }

  async initConnection() {
    this.guard();
    let connection;
    try {
      connection = this.track(await this.createConnection());
      this.guard();
      await this.waitFor(new Promise((resolve, reject) => {
        connection.connect(error => error ? reject(error) : resolve(connection));
      })).promise;
      this.guard();
      await this.waitFor(this.execute(connection, this.buildAlterSessionSql({
        TIMEZONE: 'UTC',
        STATEMENT_TIMEOUT_IN_SECONDS: this.config.executionTimeout,
        QUOTED_IDENTIFIERS_IGNORE_CASE: this.config.identIgnoreCase,
      }), [], false)).promise;
      await this.waitFor(this.execute(connection, 'USE SECONDARY ROLES NONE', [], false)).promise;
      await this.waitFor(this.execute(connection, 'ALTER SESSION SET ABORT_DETACHED_QUERY=TRUE, STATEMENT_QUEUED_TIMEOUT_IN_SECONDS=60', [], false)).promise;
      this.guard();
      return connection;
    } catch (error) {
      await this.destroyConnection(connection);
      this.connection = null;
      throw error;
    }
  }

  executeCancelable(connectionPromise, options, onComplete) {
    const observed = Promise.resolve(connectionPromise).then(connection => this.track(connection));
    this.connectionWaits.add(observed);
    observed.then(
      () => this.connectionWaits.delete(observed),
      () => this.connectionWaits.delete(observed),
    );
    const waiting = this.waitFor(observed);
    const guarded = waiting.promise.then(connection => new Proxy(connection, {
      get: (target, property) => {
        if (property === 'execute') return sdkOptions => {
          try {
            // Recheck synchronously at the actual SDK dispatch, after all awaits.
            this.guard();
          } catch (error) {
            void this.release().catch(() => {});
            throw error;
          }
          return target.execute(sdkOptions);
        };
        const value = Reflect.get(target, property, target);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    }));
    const original = super.executeCancelable(guarded, options, onComplete);
    const cancel = original.cancel;
    original.cancel = async () => {
      waiting.abort(Error('Query was cancelled'));
      await cancel();
    };
    return original;
  }

  release() {
    if (this.releasePromise) return this.releasePromise;
    this.closed = true;
    const pending = [this.connection, ...this.connectionWaits].filter(Boolean);
    this.connection = null;
    for (const abort of [...this.waiters]) abort(Error('Snowflake driver released'));
    const tasks = [
      ...[...this.connections].map(connection => this.destroyConnection(connection)),
      ...pending.map(promise => Promise.resolve(promise).then(
        connection => this.destroyConnection(connection),
        () => {}, // A failed connection has no additional handle to destroy.
      )),
    ];
    this.releasePromise = Promise.allSettled(tasks).then(results => {
      const failure = results.find(result => result.status === 'rejected');
      if (failure) throw failure.reason;
      if (this.destroyFailures.length) throw this.destroyFailures[0];
    });
    return this.releasePromise;
  }
}

module.exports = {LifecycleSnowflakeDriver};
