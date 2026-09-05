# Third-party notices

MERIDIAN does not vendor the IO-VNBD dataset or OpenStreetMap extracts. Users who download data, tiles, or dependencies must comply with the applicable upstream terms and licenses.

## IO-VNBD

- Source: [IO-VNBD](https://github.com/onyekpeu/IO-VNBD)
- Use in this repository: local download for timestamp synchronization, calibration, velocity training, and offline evaluation.
- Status: raw dataset files are deliberately not committed. Consult the upstream repository for its citation and license terms before redistributing any data.

## STRIDE road-safety dataset

- Source: [Harnessing Smartphone Sensors for Enhanced Road Safety: A Comprehensive Dataset](https://doi.org/10.6084/m9.figshare.25460755.v4)
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- Use in this repository: a quality-audited driving session provides secondary phone-domain supervision for the runtime-equivalent velocity model. Raw files are deliberately not committed.

## OpenStreetMap

- Source: [OpenStreetMap](https://www.openstreetmap.org/copyright)
- Use in this repository: offline road geometry for map matching and raster tiles from `tile.openstreetmap.org` in the Flutter map.
- Attribution: public/demo builds must visibly credit `© OpenStreetMap contributors` and comply with the [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/). The mobile navigation view displays the required in-map attribution.

## Software dependencies

The Flutter, Python, ONNX Runtime, PyTorch, and map-library dependencies are declared in the relevant `pubspec.yaml`, requirements files, and package manifests. Their licenses remain with their respective authors.
