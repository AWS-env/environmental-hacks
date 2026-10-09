// Synthetic fixture for OBS-12: JS exceptions and suppressions.
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const config = require('./otel-config');

// getNodeAutoInstrumentations() is only mentioned in this comment.
const label = 'getNodeAutoInstrumentations()';

const fromConfig = getNodeAutoInstrumentations(config);

// noqa: OBS-12 -- the service is small and wants every instrumentation
const suppressed = getNodeAutoInstrumentations();

const otherRule = getNodeAutoInstrumentations(); // noqa: no-unused-vars

module.exports = { label, fromConfig, suppressed, otherRule };
