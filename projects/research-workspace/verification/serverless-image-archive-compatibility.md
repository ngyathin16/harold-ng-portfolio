# Exact image archive compatibility

Build request 04 stopped before Google authentication or registry upload. Its Ubuntu 24.04 runner installed Skopeo 1.13.3, whose OCI archive reader rejected the Docker schema2 manifest media type. Archive framing, tags, hashes and decompressed layer validation had passed. The failed artifact record remains retained; the build is not rerun.

The corrected workflow exports standard OCI media types explicitly:

```text
type=oci,oci-mediatypes=true,compression=gzip,force-compression=true,dest=<local-archive>
```

The validator requires OCI image manifest, configuration and gzip layer media types. It still requires one Linux amd64 image, exact source labels and tag, a complete hash-verified local blob closure, and bounded gzip expansion. Full compressed archive sizes remain the storage allowance; the independent uncompressed pair limit remains 2 GiB.

Before authentication, the actual runner must copy each archive into a local directory using `skopeo copy --preserve-digests --retry-times 0`, then verify the copied manifest and every blob against the archive. Registry publication uses the same archive and digest-preservation flags. A failed local compatibility check prevents authentication and upload. A GitHub rerun is rejected; another build requires a new reviewed request and retained-storage calculation.

Build request 05 succeeded for commit `de0ae1e2cf1e0500ae70f898876f8e7b7d01934f`. The actual Ubuntu runner's Skopeo 1.13.3 copied both standard OCI archives locally with preserved manifest and blob hashes before Google authentication, then published those exact immutable digests. The [image build](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/45736242751) and [Research checks](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/45736242720) both passed on their first attempt. The updated artifact suite passes 43 tests, including rejection of Docker schema2 content inside the OCI archive; Actionlint also passes.

The two full compressed archives measure 169,176,576 and 298,312,704 bytes. Added to the unchanged 923,179,605-byte retained-storage floor, the conservative total is **1,390,668,885 bytes**, below the 2 GiB registry allowance. The expanded layer streams total 1,337,372,160 bytes and also fit their separate 2 GiB guard. These are archive and layer measurements, not a provider invoice. Exact digests, archive hashes, runner version and verification timestamps are recorded in [the sanitized build evidence](serverless-image-build-resilience.json).

The separate [hosted runtime readback](serverless-resilience-runtime.json) now verifies these exact images and commit across all nine runtime templates, converged service revisions and ready traffic. A private `/ready` request returned HTTP 200 with search paused at 01:54 UTC on November 26. Migration 012 is installed. All four schedules and public access remained disabled at the 02:10 UTC audit despite the configured November 26, 03:00 UTC to November 27, 05:00 UTC finite window. This establishes deployment and paused readiness, not full-search, retained-result recovery, 24-hour or recurring-cost acceptance.

Google explicitly accepts OCI images in the [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract) and [Artifact Registry Docker repositories](https://docs.cloud.google.com/artifact-registry/docs/supported-formats). Docker documents the [OCI exporter media-type option](https://docs.docker.com/build/exporters/oci-docker/). Skopeo documents that [digest-preserving copy fails when preservation is impossible](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md).
