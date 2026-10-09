// Synthetic fixture for OBS-12: an empty config object still enables everything.
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';

export const all = getNodeAutoInstrumentations({ /* nothing set */ });

export const trimmed = getNodeAutoInstrumentations({ '@opentelemetry/instrumentation-dns': { enabled: false } });

export const again = getNodeAutoInstrumentations(
);
