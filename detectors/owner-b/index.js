'use strict';

const db34 = require('./db-34-detector');

module.exports = {
  db34: {
    scanSource: db34.scanSource,
    scanFile: db34.scanFile
  },
  // Default scanner for Owner B static scanners
  scanSource: db34.scanSource,
  scanFile: db34.scanFile
};
