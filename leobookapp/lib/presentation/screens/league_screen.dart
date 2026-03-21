// league_screen.dart: League detail page with Overview, Fixtures, Results, Predictions, Stats, Archive tabs.
// Part of LeoBook App — Screens

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:leobookapp/core/constants/app_colors.dart';
import 'package:leobookapp/data/models/league_model.dart';
import 'package:leobookapp/data/repositories/data_repository.dart';
import '../widgets/shared/leo_tab.dart';
import '../widgets/shared/league_tabs/overview_tab.dart';
import '../widgets/shared/league_tabs/fixtures_tab.dart';
import '../widgets/shared/league_tabs/results_tab.dart';
import '../widgets/shared/league_tabs/predictions_tab.dart';
import '../widgets/shared/league_tabs/stats_tab.dart';
import '../widgets/shared/league_tabs/archive_tab.dart';

class LeagueScreen extends StatefulWidget {
  final String leagueId;
  final String leagueName;
  final String? season; // Optional: when viewing archived season

  const LeagueScreen({
    super.key,
    required this.leagueId,
    required this.leagueName,
    this.season,
  });

  @override
  State<LeagueScreen> createState() => _LeagueScreenState();
}

class _LeagueScreenState extends State<LeagueScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final DataRepository _repo = DataRepository();
  LeagueModel? _league;

  bool get _isArchiveView => widget.season != null;

  // Archive view shows only 3 tabs: Results, Stats, Standings (no fixtures/predictions)
  int get _tabCount => _isArchiveView ? 3 : 6;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: _tabCount, vsync: this);
    _loadLeagueData();
  }

  Future<void> _loadLeagueData() async {
    final league = await _repo.fetchLeagueById(widget.leagueId);
    if (mounted) {
      setState(() => _league = league);
    }
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  List<Widget> _buildTabs() {
    if (_isArchiveView) {
      return [
        Tab(child: LeoTab(text: "RESULTS", isSelected: _tabController.index == 0)),
        Tab(child: LeoTab(text: "STATS", isSelected: _tabController.index == 1)),
        Tab(child: LeoTab(text: "PREDICTIONS", isSelected: _tabController.index == 2)),
      ];
    }
    return [
      Tab(child: LeoTab(text: "OVERVIEW", isSelected: _tabController.index == 0)),
      Tab(child: LeoTab(text: "FIXTURES", isSelected: _tabController.index == 1)),
      Tab(child: LeoTab(text: "RESULTS", isSelected: _tabController.index == 2)),
      Tab(child: LeoTab(text: "PREDICTIONS", isSelected: _tabController.index == 3)),
      Tab(child: LeoTab(text: "STATS", isSelected: _tabController.index == 4)),
      Tab(child: LeoTab(text: "ARCHIVE", isSelected: _tabController.index == 5)),
    ];
  }

  List<Widget> _buildTabViews() {
    if (_isArchiveView) {
      return [
        LeagueResultsTab(leagueId: widget.leagueId, leagueName: widget.leagueName, season: widget.season),
        LeagueStatsTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
        LeaguePredictionsTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
      ];
    }
    return [
      LeagueOverviewTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
      LeagueFixturesTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
      LeagueResultsTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
      LeaguePredictionsTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
      LeagueStatsTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
      LeagueArchiveTab(leagueId: widget.leagueId, leagueName: widget.leagueName),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final displayTitle = _isArchiveView
        ? '${widget.leagueName} • ${widget.season}'
        : widget.leagueName;

    return Scaffold(
      backgroundColor:
          isDark ? AppColors.neutral900 : AppColors.neutral700,
      body: NestedScrollView(
        headerSliverBuilder: (context, innerBoxIsScrolled) {
          return [
            SliverAppBar(
              backgroundColor: isDark
                  ? AppColors.neutral900.withValues(alpha: 0.9)
                  : AppColors.neutral700.withValues(alpha: 0.9),
              surfaceTintColor: Colors.transparent,
              pinned: true,
              floating: true,
              snap: true,
              elevation: 0,
              toolbarHeight: 64,
              leading: IconButton(
                icon: Icon(
                  Icons.arrow_back_ios_new,
                  size: 20,
                  color: isDark ? Colors.white : AppColors.textDark,
                ),
                onPressed: () => Navigator.pop(context),
              ),
              titleSpacing: 0,
              title: Row(
                children: [
                  Container(
                    width: 32,
                    height: 32,
                    margin: const EdgeInsets.only(right: 10),
                    decoration: BoxDecoration(
                      color: isDark ? AppColors.neutral800 : Colors.white,
                      shape: BoxShape.circle,
                      border: Border.all(
                        color: isDark
                            ? Colors.white.withValues(alpha: 0.1)
                            : Colors.black.withValues(alpha: 0.1),
                      ),
                    ),
                    child: _league?.crest != null &&
                            _league!.crest!.startsWith('http')
                        ? ClipOval(
                            child: CachedNetworkImage(
                              imageUrl: _league!.crest!,
                              width: 32,
                              height: 32,
                              fit: BoxFit.cover,
                              placeholder: (_, __) => const Icon(
                                Icons.emoji_events_outlined,
                                size: 18,
                                color: AppColors.primary,
                              ),
                              errorWidget: (_, __, ___) => const Icon(
                                Icons.emoji_events_outlined,
                                size: 18,
                                color: AppColors.primary,
                              ),
                            ),
                          )
                        : const Icon(
                            Icons.emoji_events_outlined,
                            size: 18,
                            color: AppColors.primary,
                          ),
                  ),
                  Expanded(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          displayTitle,
                          style: GoogleFonts.lexend(
                            fontSize: 14,
                            fontWeight: FontWeight.w700,
                            color: isDark ? Colors.white : AppColors.textDark,
                            height: 1.0,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          (_league?.currentSeason ?? widget.season ?? '').toUpperCase(),
                          style: GoogleFonts.lexend(
                            fontSize: 10,
                            fontWeight: FontWeight.w700,
                            color: AppColors.textGrey,
                            letterSpacing: 1.0,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              actions: const [],
              bottom: PreferredSize(
                preferredSize: const Size.fromHeight(50),
                child: Container(
                  decoration: BoxDecoration(
                    border: Border(
                      bottom: BorderSide(
                        color: isDark
                            ? Colors.white.withValues(alpha: 0.1)
                            : Colors.black.withValues(alpha: 0.05),
                      ),
                    ),
                  ),
                  child: AnimatedBuilder(
                    animation: _tabController,
                    builder: (context, _) {
                      return TabBar(
                        controller: _tabController,
                        isScrollable: true,
                        tabAlignment: TabAlignment.start,
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        labelColor: AppColors.primary,
                        unselectedLabelColor: AppColors.textGrey,
                        indicatorColor: AppColors.primary,
                        indicatorSize: TabBarIndicatorSize.label,
                        indicatorWeight: 3,
                        dividerColor: Colors.transparent,
                        tabs: _buildTabs(),
                      );
                    },
                  ),
                ),
              ),
            ),
          ];
        },
        body: TabBarView(
          controller: _tabController,
          children: _buildTabViews(),
        ),
      ),
    );
  }
}
