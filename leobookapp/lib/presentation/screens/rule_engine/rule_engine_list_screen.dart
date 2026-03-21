// rule_engine_list_screen.dart: List of all saved rule engines.
// Part of LeoBook App — Rule Engine Screens
//
// Classes: RuleEngineListScreen, _RuleEngineListScreenState

import 'package:flutter/material.dart';
import 'package:leobookapp/core/constants/app_colors.dart';
import 'package:leobookapp/core/constants/responsive_constants.dart';
import 'package:leobookapp/core/widgets/glass_container.dart';
import 'package:leobookapp/data/models/rule_config_model.dart';
import 'package:leobookapp/data/services/leo_service.dart';
import 'package:leobookapp/core/widgets/leo_loading_indicator.dart';
import 'rule_editor_screen.dart';

class RuleEngineListScreen extends StatefulWidget {
  const RuleEngineListScreen({super.key});

  @override
  State<RuleEngineListScreen> createState() => _RuleEngineListScreenState();
}

class _RuleEngineListScreenState extends State<RuleEngineListScreen> {
  final LeoService _service = LeoService();
  List<RuleConfigModel> _engines = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadEngines();
  }

  Future<void> _loadEngines() async {
    setState(() => _loading = true);
    try {
      _engines = await _service.loadAllEngines();
    } catch (e) {
      debugPrint('Error loading engines: $e');
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _setDefault(String engineId) async {
    await _service.setDefaultEngine(engineId);
    await _loadEngines();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Default engine updated')),
      );
    }
  }

  Future<void> _deleteEngine(RuleConfigModel engine) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete Engine?'),
        content: Text('Delete "${engine.name}"? This cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirm == true) {
      final ok = await _service.deleteEngine(engine.id);
      if (ok) {
        await _loadEngines();
      } else if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Cannot delete the last engine')),
        );
      }
    }
  }

  void _openEditor([RuleConfigModel? engine]) async {
    final result = await Navigator.push<bool>(
      context,
      MaterialPageRoute(
        builder: (_) => RuleEditorScreen(engine: engine),
      ),
    );
    if (result == true) await _loadEngines();
  }

  @override
  Widget build(BuildContext context) {
    final isDesktop = Responsive.isDesktop(context);
    final pad = Responsive.horizontalPadding(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Rule Engines'),
        centerTitle: true,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadEngines,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _openEditor(),
        icon: const Icon(Icons.add),
        label: const Text('New Engine'),
        backgroundColor: AppColors.success,
      ),
      body: _loading
          ? const LeoLoadingIndicator()
          : _engines.isEmpty
              ? const Center(child: Text('No engines found'))
              : RefreshIndicator(
                  onRefresh: _loadEngines,
                  child: ListView.builder(
                    padding: EdgeInsets.symmetric(
                      horizontal: pad,
                      vertical: 16,
                    ),
                    itemCount: _engines.length,
                    itemBuilder: (ctx, i) =>
                        _buildEngineCard(_engines[i], isDesktop),
                  ),
                ),
    );
  }

  Widget _buildEngineCard(RuleConfigModel engine, bool isDesktop) {
    final acc = engine.accuracy;
    final hasStats = acc.totalPredictions > 0;
    final winRate = hasStats ? '${acc.winRate.toStringAsFixed(1)}%' : '—';
    final total =
        hasStats ? '${acc.correct}/${acc.totalPredictions}' : 'Not tested';

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: GlassContainer(
        onTap: () => _openEditor(engine),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header row
              Row(
                children: [
                  if (engine.isDefault)
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 2,
                      ),
                      margin: const EdgeInsets.only(right: 8),
                      decoration: BoxDecoration(
                        color: AppColors.success.withValues(alpha: 0.2),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        '⭐ DEFAULT',
                        style: TextStyle(
                          color: AppColors.success,
                          fontSize: 10,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                  Expanded(
                    child: Text(
                      engine.name,
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w600,
                        color: AppColors.textPrimary,
                      ),
                    ),
                  ),
                  PopupMenuButton<String>(
                    onSelected: (val) {
                      if (val == 'default') _setDefault(engine.id);
                      if (val == 'delete') _deleteEngine(engine);
                    },
                    itemBuilder: (_) => [
                      if (!engine.isDefault)
                        const PopupMenuItem(
                          value: 'default',
                          child: Text('Set as Default'),
                        ),
                      if (!engine.isDefault)
                        const PopupMenuItem(
                          value: 'delete',
                          child: Text(
                            'Delete',
                            style: TextStyle(color: Colors.red),
                          ),
                        ),
                    ],
                  ),
                ],
              ),

              if (engine.description.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  engine.description,
                  style: TextStyle(
                    fontSize: 13,
                    color: AppColors.textGrey,
                  ),
                ),
              ],

              const SizedBox(height: 12),

              // Stats row
              Row(
                children: [
                  _statChip(Icons.show_chart, 'Accuracy', winRate),
                  const SizedBox(width: 12),
                  _statChip(Icons.format_list_numbered, 'Predictions', total),
                  const SizedBox(width: 12),
                  _statChip(
                    Icons.public,
                    'Scope',
                    engine.scope.displayLabel,
                  ),
                  const SizedBox(width: 12),
                  _statChip(
                    Icons.shield_outlined,
                    'Risk',
                    engine.riskPreference[0].toUpperCase() +
                        engine.riskPreference.substring(1),
                  ),
                ],
              ),

              if (acc.backtestPeriod != null) ...[
                const SizedBox(height: 8),
                Text(
                  'Last backtested: ${acc.backtestPeriod}',
                  style: TextStyle(
                    fontSize: 11,
                    color: AppColors.textTertiary,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _statChip(IconData icon, String label, String value) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 14, color: AppColors.textTertiary),
              const SizedBox(width: 4),
              Text(
                label,
                style: TextStyle(
                  fontSize: 10,
                  color: AppColors.textTertiary,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }
}
