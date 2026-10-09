'use strict';

const db34 = require('./db-34-detector');

module.exports = {
  evaluate: require('./core/g01').evaluate,
  checks: {
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
