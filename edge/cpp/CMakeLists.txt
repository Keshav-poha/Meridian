cmake_minimum_required(VERSION 3.21)
project(meridian_idr_edge LANGUAGES CXX)

add_library(meridian_idr_edge INTERFACE)
target_compile_features(meridian_idr_edge INTERFACE cxx_std_17)
target_include_directories(meridian_idr_edge INTERFACE include)

# Stage 10 adds an ONNX Runtime target here. This placeholder intentionally
# builds without vendor dependencies so contract scaffolding is testable now.
