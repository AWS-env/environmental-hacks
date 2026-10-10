# Synthetic fixture for OBS-10: Datadog log index with exclusion filters.
resource "datadog_logs_index" "main" {
  name = "main"
  filter {
    query = "*"
  }

  exclusion_filter {
    name       = "drop-debug"
    is_enabled = true
    filter {
      query       = "status:debug"
      sample_rate = 1.0
    }
  }

  exclusion_filter {
    name       = "sample-info"
    is_enabled = true
    filter {
      query       = "status:info service:web"
      sample_rate = 0.9
    }
  }

  exclusion_filter {
    name       = "paused"
    is_enabled = false
    filter {
      query       = "service:batch"
      sample_rate = 1.0
    }
  }

  exclusion_filter {
    name       = "health-checks"
    is_enabled = true
    filter { sample_rate = 1 }
  }
}

resource "datadog_logs_index" "audit" {
  name = "audit"
  filter {
    query = "source:audit # not a comment"
  }
  /* exclusion_filter {
    is_enabled = true
  } */
  tags = {
    team = "obs"
  }
}
