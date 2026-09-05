/// Input-quality check for the velocity CNN's training-normalized tensor.
///
/// The IO-VNBD artifact was trained on one phone/mount. A live phone whose
/// raw axes or magnetic field remain far outside that distribution must not
/// turn a neural-network extrapolation into a navigation velocity.
class VelocityModelQuality {
  static const maxP95AbsoluteZScore = 5.0;

  const VelocityModelQuality(this.p95AbsoluteZScore);

  final double p95AbsoluteZScore;

  bool get isInDistribution => p95AbsoluteZScore <= maxP95AbsoluteZScore;

  static VelocityModelQuality fromNormalizedWindow(List<List<double>> window) {
    final scores = <double>[
      for (final sample in window)
        for (final value in sample) value.abs(),
    ]..sort();
    if (scores.isEmpty) return const VelocityModelQuality(double.infinity);
    final index = ((scores.length - 1) * 0.95).ceil();
    return VelocityModelQuality(scores[index]);
  }
}
