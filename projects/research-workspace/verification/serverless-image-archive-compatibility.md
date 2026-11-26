# Exact image archive compatibility

Build request 04 stopped before Google authentication or registry upload. Its Ubuntu 24.04 runner installed Skopeo 1.13.3, whose OCI archive reader rejected the Docker schema2 manifest media type. Archive framing, tags, hashes and decompressed layer validation had passed. The failed artifact record remains retained; the build is not rerun.

The corrected workflow exports standard OCI media types explicitly:

```text
type=oci,oci-mediatypes=true,compression=gzip,force-compression=true,dest=<local-archive>
```

The validator requires OCI image manifest, configuration and gzip layer media types. It still requires one Linux amd64 image, exact source labels and tag, a complete hash-verified local blob closure, and bounded gzip expansion. Full compressed archive sizes remain the storage allowance; the independent uncompressed pair limit remains 2 GiB.

Before authentication, the actual runner must copy each archive into a local directory using `skopeo copy --preserve-digests --retry-times 0`, then verify the copied manifest and every blob against the archive. Registry publication uses the same archive and digest-preservation flags. A failed local compatibility check prevents authentication and upload. A GitHub rerun is rejected; another build requires a new reviewed request and retained-storage calculation.

The updated artifact suite passes 43 tests, including rejection of Docker schema2 content inside the OCI archive. Actionlint passes. A successful corrected runner build and hosted deployment remain separate qualification steps.

Google explicitly accepts OCI images in the [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract) and [Artifact Registry Docker repositories](https://docs.cloud.google.com/artifact-registry/docs/supported-formats). Docker documents the [OCI exporter media-type option](https://docs.docker.com/build/exporters/oci-docker/). Skopeo documents that [digest-preserving copy fails when preservation is impossible](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md).
