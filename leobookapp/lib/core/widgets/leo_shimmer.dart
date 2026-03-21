// leo_shimmer.dart: Skeleton shimmer loading placeholders for LeoBook.
// Part of LeoBook App — Core Widgets

import 'package:flutter/material.dart';
import 'package:leobookapp/core/constants/app_colors.dart';

/// Animated shimmer effect for skeleton loading states.
/// Wraps any child to give it a pulsing shimmer appearance.
class LeoShimmer extends StatefulWidget {
  final Widget child;

  const LeoShimmer({super.key, required this.child});

  @override
  State<LeoShimmer> createState() => _LeoShimmerState();
}

class _LeoShimmerState extends State<LeoShimmer>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  late Animation<double> _animation;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
    _animation = CurvedAnimation(parent: _controller, curve: Curves.easeInOut);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _animation,
      builder: (context, child) {
        return Opacity(
          opacity: 0.3 + (_animation.value * 0.4),
          child: child,
        );
      },
      child: widget.child,
    );
  }
}

/// Pre-built skeleton for a match card loading state.
class MatchCardSkeleton extends StatelessWidget {
  const MatchCardSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return LeoShimmer(
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppColors.neutral800,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: Colors.white.withValues(alpha: 0.06)),
        ),
        child: Row(
          children: [
            _circle(32),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [_bar(100, 10), const SizedBox(height: 8), _bar(70, 8)],
              ),
            ),
            Column(
              children: [_bar(24, 14), const SizedBox(height: 6), _bar(40, 8)],
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [_bar(100, 10), const SizedBox(height: 8), _bar(70, 8)],
              ),
            ),
            const SizedBox(width: 10),
            _circle(32),
          ],
        ),
      ),
    );
  }

  static Widget _bar(double w, double h) => Container(
        width: w,
        height: h,
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(h / 2),
        ),
      );

  static Widget _circle(double d) => Container(
        width: d,
        height: d,
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.08),
          shape: BoxShape.circle,
        ),
      );
}

/// Skeleton list — shows N match card skeletons.
class MatchListSkeleton extends StatelessWidget {
  final int count;
  const MatchListSkeleton({super.key, this.count = 5});

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.only(top: 16),
      itemCount: count,
      itemBuilder: (_, __) => const MatchCardSkeleton(),
    );
  }
}

/// Generic content skeleton — shows a few bars for text loading.
class ContentSkeleton extends StatelessWidget {
  const ContentSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return LeoShimmer(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _bar(160, 14),
            const SizedBox(height: 16),
            _bar(double.infinity, 10),
            const SizedBox(height: 10),
            _bar(double.infinity, 10),
            const SizedBox(height: 10),
            _bar(200, 10),
            const SizedBox(height: 24),
            _bar(120, 14),
            const SizedBox(height: 16),
            _bar(double.infinity, 10),
            const SizedBox(height: 10),
            _bar(240, 10),
          ],
        ),
      ),
    );
  }

  static Widget _bar(double w, double h) => Container(
        width: w,
        height: h,
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(h / 2),
        ),
      );
}
