'use strict';

const db34 = require('./db-34-detector');

module.exports = {
  evaluate: require('./core/dispatch').evaluate,
  checks: {
    'DB-16': require('./checks/db-16'),
    'DB-05': require('./checks/db-05'),
    'DB-06': require('./checks/db-06'),
    ...require('./core/dispatch').jobs,
    'DB-34': require('./checks/db-34'),
  },
  db34: {
    scanSource: db34.scanSource,
    scanFile: db34.scanFile
  },
  // Legacy compatibility scanners; deployed/shared-contract callers use evaluate.
  scanSource: db34.scanSource,
  scanFile: db34.scanFile
};
