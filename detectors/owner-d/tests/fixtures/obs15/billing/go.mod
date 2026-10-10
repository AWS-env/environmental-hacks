// Synthetic fixture (OBS15-01): Go service with two tracers.
module example.com/billing

go 1.22

require (
	github.com/go-chi/chi/v5 v5.0.12
	gopkg.in/DataDog/dd-trace-go.v1 v1.64.1
	go.opentelemetry.io/otel v1.27.0
	go.opentelemetry.io/otel/sdk v1.27.0
	go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.52.0
	github.com/newrelic/go-agent/v3 v3.33.0 // indirect
)
