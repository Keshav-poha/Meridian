/// Input-quality check for the velocity CNN's training-normalized tensor.
///
/// The IO-VNBD artifact was trained on one phone/mount. A live phone whose
/// raw axes or magnetic field remain far outside that distribution must not
/// turn a neural-network extrapolation into a navigation velocity.
class VelocityModelQuality {
  static const maxP95AbsoluteZScore = 5.0;
  static const maxAllowedAbsoluteZScore = 12.0;

  const VelocityModelQuality(this.p95AbsoluteZScore, this.maxAbsoluteZScore);

  final double p95AbsoluteZScore;
  final double maxAbsoluteZScore;

  bool get isInDistribution =>
      p95AbsoluteZScore.isFinite &&
      maxAbsoluteZScore.isFinite &&
      p95AbsoluteZScore <= maxP95AbsoluteZScore &&
      maxAbsoluteZScore <= VelocityModelQuality.maxAllowedAbsoluteZScore;

  /// A continuous, input-only contribution to prediction confidence.
  ///
  /// This deliberately becomes zero for a rejected window; downstream code
  /// combines it with mount, motion, and GNSS/DR health rather than treating
  /// it as a complete uncertainty estimate.
  double get confidence {
    if (!isInDistribution) return 0;
    final bulk = 1 - p95AbsoluteZScore / maxP95AbsoluteZScore;
    final spike =
        1 - maxAbsoluteZScore / VelocityModelQuality.maxAllowedAbsoluteZScore;
    return (bulk < spike ? bulk : spike).clamp(0.0, 1.0).toDouble();
  }

  static VelocityModelQuality fromNormalizedWindow(List<List<double>> window) {
    final scores = <double>[
      for (final sample in window)
        for (final value in sample) value.abs(),
    ]..sort();
    if (scores.isEmpty) {
      return const VelocityModelQuality(double.infinity, double.infinity);
    }
    final index = ((scores.length - 1) * 0.95).ceil();
    return VelocityModelQuality(scores[index], scores.last);
  }
}
