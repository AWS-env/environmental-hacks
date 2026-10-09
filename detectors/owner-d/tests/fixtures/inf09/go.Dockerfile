# Synthetic INF-09 fixture: Go service built and run in the toolchain image.
FROM golang:1.22
WORKDIR /src
COPY . .
RUN go build -o /usr/local/bin/app .
CMD ["app"]
