"""Figuras de análise persistidas (BE-33) — cômputo, fingerprint, staleness.

A figura guarda a receita (``spec``) e um ``result_cache`` regenerável:
``compute_figure`` resolve as populações por caminho de nomes em cada
amostra dos grupos — mesma regra de casamento do ``derive_analysis``/
``goToAdjacentFile`` do front (percorre a árvore por nome, nível a
nível) — e monta as linhas a partir do ``analysis_result`` persistido
dos gates (stats de raiz para a população ``"file"``).

``resolved_inputs`` é o fingerprint de staleness: ``is_stale`` é true
quando existe ``AnalysisRevision`` mais recente que ``result_revision``
cujo alvo intersecta os gates/amostras resolvidos — ou ação
experiment-wide, que pode mudar stats de qualquer gate sem aparecer no
conjunto resolvido. Renomear um gate que a figura não usa não marca
stale; renomear um gate que ela usa marca (e o recompute reporta o
caminho quebrado em ``unmatched``/``removed_since_last``).
"""

from __future__ import annotations

import hashlib
import json
import math
from itertools import combinations

from django.utils import timezone
from scipy import stats as scipy_stats

from analytics.models import (
    AnalysisFigure,
    AnalysisResult,
    AnalysisRevision,
    GateModel,
)
from fcs_parser.models import FileDataModel

ROOT_POPULATION = "file"

# Ações que podem mudar stats de qualquer gate/amostra do experimento sem
# aparecer no conjunto resolvido da figura — sempre marcam stale.
EXPERIMENT_WIDE_ACTIONS = {
    AnalysisRevision.ACTION_APPLY,
    AnalysisRevision.ACTION_MERGE,
    AnalysisRevision.ACTION_DERIVE,
    AnalysisRevision.ACTION_COMPENSATION_APPLY,
    AnalysisRevision.ACTION_COMPENSATION_REMOVE,
    AnalysisRevision.ACTION_MOVE_SUBSAMPLE,
    AnalysisRevision.ACTION_RESTORE,
}

METRIC_SUMMARY = {"percent_parent", "percent_total"}
METRIC_CHANNEL = {"mean_mfi", "median_mfi", "std_dev", "cv", "rcv"}

STATS_TEST_CHOICES = {"auto", "parametric", "nonparametric"}
_STATS_CHART_TYPES = {
    AnalysisFigure.CHART_STATS_BAR,
    AnalysisFigure.CHART_STATS_STRIP,
}
# n mínimo para um grupo entrar nos testes entre grupos (§7.5).
_STATS_MIN_N = 3
VALID_METRICS = METRIC_SUMMARY | METRIC_CHANNEL

_SUMMARY_FIELD = {
    "percent_parent": "percent_of_parent_population",
    "percent_total": "percent_of_total_population",
}


def split_population_path(population: str) -> list[str]:
    """Divide o caminho de nomes do spec em segmentos.

    Contrato: ``"Lymphocytes/CD3/CD4"``. Aceita ``" > "`` (separador do
    front) como fallback para robustez — normalizado na validação.
    """
    if " > " in population and "/" not in population:
        parts = population.split(" > ")
    else:
        parts = population.split("/")
    return [p.strip() for p in parts if p.strip()]


def _resolve_population(gates_by_parent, names):
    """Percorre a árvore de gates por nome, nível a nível.

    Devolve o GateModel do último segmento ou None se o caminho quebrar
    em qualquer nível — caminho parcial não resolve (diferente do
    ``nearest=True`` do front, que cai no ancestral: aqui a figura pede
    a população exata).
    """
    level = gates_by_parent.get(None, [])
    node = None
    for name in names:
        node = next((g for g in level if g.name == name), None)
        if node is None:
            return None
        level = gates_by_parent.get(node.id, [])
    return node


def _file_is_ready(file_data) -> bool:
    """Amostra materializada: tem Parquet (L2) ou data_set legado no banco."""
    return bool(file_data.parquet_path) or bool(file_data.data_set)


def spec_fingerprint(spec: dict) -> str:
    """Hash estável do spec — muda quando grupos/populações/métrica/canal
    mudam; usado para detectar edição de spec sem recompute."""
    payload = json.dumps(spec or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _metric_value(analysis_result, metric, channel):
    """Extrai o valor da métrica do payload de stats persistido.

    Devolve None quando indisponível (canal ausente, gate não-avaliável)
    — a linha fica ausente no cache, nunca zero (regra do export).
    """
    if not analysis_result or analysis_result.get("applicable") is False:
        return None
    if metric in METRIC_SUMMARY:
        return (analysis_result.get("summary_metrics") or {}).get(
            _SUMMARY_FIELD[metric]
        )
    channel_stats = (analysis_result.get("channel_statistics") or {}).get(channel)
    if not channel_stats:
        return None
    return channel_stats.get(metric)


def _compute_root_stats(file_data) -> dict:
    """Stats da amostra inteira (população ``"file"``) — mesmo cálculo do
    FileStatsView: dataset compensado quando houver matriz aplicada."""
    from analytics.tasks import calculate_cytometry_metrics
    from fcs_parser.services.compensation import (
        applied_compensation,
        apply_compensation,
    )
    from utils.density import normalize_columns

    dataset = normalize_columns(file_data.get_dataframe())
    applied = applied_compensation(file_data.experiment)
    if applied:
        dataset = apply_compensation(dataset, applied.channels, applied.matrix)
    if dataset.empty:
        return {"applicable": False, "reason": "empty_dataset"}
    return calculate_cytometry_metrics(
        dataset, len(dataset), dataset, list(dataset.columns)
    )


def resolution_branch(figure) -> "AnalysisBranch":
    """Branch onde as populações resolvem — a do spec ou a main."""
    if figure.branch_id:
        return figure.branch
    from analytics.services.branches import ensure_main_branch

    return ensure_main_branch(figure.experiment)


def compute_figure(figure: AnalysisFigure) -> dict:
    """Computa o ``result_cache`` da figura a partir do spec atual.

    Não persiste — o chamador decide quando gravar (create/recompute).
    Devolve o payload do cache com ``rows``, ``resolved_inputs``
    (fingerprint), ``unmatched`` e ``meta``.
    """
    spec = figure.spec or {}
    groups = spec.get("groups") or []
    populations = spec.get("populations") or []
    metric = spec.get("metric")
    channel = spec.get("channel")
    chart_type = figure.chart_type

    branch = resolution_branch(figure)

    file_ids = [
        fd_id for group in groups for fd_id in (group.get("file_data_ids") or [])
    ]
    files = {
        fd.id: fd
        for fd in FileDataModel.objects.filter(id__in=file_ids).only(
            "id",
            "file_name",
            "experiment_id",
            "active",
            "parquet_path",
            "data_set",
        )
    }

    all_gates = list(
        GateModel.objects.filter(file_data_id__in=list(files), branch=branch).only(
            "id", "name", "file_data_id", "parent_id"
        )
    )
    # árvore por amostra: parent_id -> [gates]
    gates_by_file: dict[int, dict] = {}
    for g in all_gates:
        gates_by_file.setdefault(g.file_data_id, {}).setdefault(g.parent_id, []).append(
            g
        )

    analysis_map = {
        ar.gate_id: ar.analysis_result
        for ar in AnalysisResult.objects.filter(gate_id__in=[g.id for g in all_gates])
    }

    rows = []
    unmatched_populations = set()
    unmatched_files = []
    processing_files = 0
    resolved_gate_ids = set()
    resolved_file_ids = set()
    resolved_pairs = set()

    for group in groups:
        for fd_id in group.get("file_data_ids") or []:
            fd = files.get(fd_id)
            if fd is None or not fd.active:
                unmatched_files.append(
                    {"file_data_id": fd_id, "file_name": getattr(fd, "file_name", None)}
                )
                continue
            if not _file_is_ready(fd):
                processing_files += 1
            resolved_file_ids.add(fd.id)
            if chart_type == AnalysisFigure.CHART_DISTRIBUTION:
                # v1: o cache da distribuição guarda só o fingerprint —
                # as curvas vêm do densityService live na leitura.
                for pop in populations:
                    if pop == ROOT_POPULATION:
                        resolved_pairs.add((pop, fd.id))
                        continue
                    names = split_population_path(pop)
                    gate = _resolve_population(gates_by_file.get(fd.id, {}), names)
                    if gate is None:
                        unmatched_populations.add(pop)
                    else:
                        resolved_gate_ids.add(gate.id)
                        resolved_pairs.add((pop, fd.id))
                continue
            for pop in populations:
                if pop == ROOT_POPULATION:
                    stats = _compute_root_stats(fd)
                    value = _metric_value(stats, metric, channel)
                    if value is not None:
                        rows.append(
                            {
                                "group": group.get("name"),
                                "file_data_id": fd.id,
                                "file_name": fd.file_name,
                                "population": pop,
                                "value": value,
                            }
                        )
                        resolved_pairs.add((pop, fd.id))
                    else:
                        unmatched_populations.add(pop)
                    continue
                names = split_population_path(pop)
                gate = _resolve_population(gates_by_file.get(fd.id, {}), names)
                if gate is None:
                    unmatched_populations.add(pop)
                    continue
                resolved_gate_ids.add(gate.id)
                value = _metric_value(analysis_map.get(gate.id), metric, channel)
                if value is not None:
                    rows.append(
                        {
                            "group": group.get("name"),
                            "file_data_id": fd.id,
                            "file_name": fd.file_name,
                            "population": pop,
                            "value": value,
                        }
                    )
                    resolved_pairs.add((pop, fd.id))
                else:
                    # Gate existe mas não é avaliável nesta amostra —
                    # ausente, não zero.
                    unmatched_populations.add(pop)

    meta = {
        "n_por_grupo": {
            g.get("name"): len(g.get("file_data_ids") or []) for g in groups
        },
        "computed_at": timezone.now().isoformat(),
        "spec_fingerprint": spec_fingerprint(spec),
    }
    if processing_files:
        meta["warnings"] = [
            f"{processing_files} "
            f'{"amostra" if processing_files == 1 else "amostras"} '
            "ainda processando"
        ]

    stats_tests = compute_stats_tests(figure, rows)

    return {
        "rows": rows,
        "resolved_pairs": sorted(
            [
                {"population": pop, "file_data_id": fd_id}
                for pop, fd_id in resolved_pairs
            ],
            key=lambda r: (r["population"], r["file_data_id"]),
        ),
        "resolved_inputs": {
            "gate_ids": sorted(resolved_gate_ids),
            "file_data_ids": sorted(resolved_file_ids),
            "channel": channel,
        },
        "unmatched": {
            "populations": sorted(unmatched_populations),
            "files": unmatched_files,
        },
        "meta": meta,
        **({"stats_tests": stats_tests} if stats_tests is not None else {}),
    }


def _clean_number(value):
    """float finito ou None — NaN/inf nunca vão para o contrato."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _bh_adjust(p_values):
    """Benjamini-Hochberg: devolve p_adj na ordem original dos p-values."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running_min = min(running_min, p_values[idx] * m / rank)
        adjusted[idx] = min(running_min, 1.0)
    return adjusted


def _resolve_test_method(preference, eligible_counts):
    """auto → paramétrico, caindo para não-paramétrico com n pequeno."""
    if preference != "auto":
        return preference, None
    if min(eligible_counts, default=0) < 10:
        return "nonparametric", "auto → não-paramétrico: grupo com n < 10"
    return "parametric", None


def _stats_tests_for_population(rows, population, groups, preference):
    """Testes entre grupos de uma população (BE-33 §7.5).

    Grupos com n < _STATS_MIN_N não entram no teste — reportados em
    ``warnings``. Devolve None quando nada é testável (a população só
    aparece na lista se houve ao menos um grupo elegível).
    """
    values_by_group = {g.get("name"): [] for g in groups}
    for row in rows:
        if row.get("population") != population:
            continue
        value = row.get("value")
        if value is not None and row.get("group") in values_by_group:
            values_by_group[row["group"]].append(value)

    n_per_group = {name: len(vals) for name, vals in values_by_group.items()}
    warnings = []
    eligible = {
        name: vals
        for name, vals in values_by_group.items()
        if len(vals) >= _STATS_MIN_N
    }
    for name, n in n_per_group.items():
        if 0 < n < _STATS_MIN_N:
            warnings.append(f'grupo "{name}" com n<3 — teste omitido')

    result = {
        "population": population,
        "method": None,
        "omnibus": None,
        "pairwise": [],
        "n_per_group": n_per_group,
        "warnings": warnings,
    }
    if len(eligible) < 2:
        warnings.append("menos de 2 grupos elegíveis — teste omitido")
        return result

    method, auto_warning = _resolve_test_method(
        preference, [len(v) for v in eligible.values()]
    )
    if auto_warning:
        warnings.append(auto_warning)
    result["method"] = method

    names = list(eligible)
    samples = [eligible[name] for name in names]
    pairwise_rows = []

    # Amostras constantes/iguais derrubam scipy (ex.: "All numbers are
    # identical" no kruskal) — vira warning, não 500.
    try:
        if method == "parametric":
            f_stat, p_omni = scipy_stats.f_oneway(*samples)
            total_n = sum(len(s) for s in samples)
            result["omnibus"] = {
                "test": "one_way_anova",
                "F": _clean_number(f_stat),
                "p": _clean_number(p_omni),
                "df": [len(samples) - 1, total_n - len(samples)],
            }
            for i, j in combinations(range(len(names)), 2):
                t_stat, p = scipy_stats.ttest_ind(
                    samples[i], samples[j], equal_var=False
                )
                pairwise_rows.append(
                    {
                        "group_a": names[i],
                        "group_b": names[j],
                        "t": _clean_number(t_stat),
                        "p": _clean_number(p),
                        "method": "welch_t",
                    }
                )
        else:
            h_stat, p_omni = scipy_stats.kruskal(*samples)
            result["omnibus"] = {
                "test": "kruskal_wallis",
                "H": _clean_number(h_stat),
                "p": _clean_number(p_omni),
                "df": [len(samples) - 1],
            }
            for i, j in combinations(range(len(names)), 2):
                u_stat, p = scipy_stats.mannwhitneyu(
                    samples[i], samples[j], alternative="two-sided"
                )
                pairwise_rows.append(
                    {
                        "group_a": names[i],
                        "group_b": names[j],
                        "U": _clean_number(u_stat),
                        "p": _clean_number(p),
                        "method": "mann_whitney_u",
                    }
                )
    except ValueError as exc:
        warnings.append(f"teste não computável: {exc}")
        return result

    raw_p = [r["p"] for r in pairwise_rows if r["p"] is not None]
    if raw_p:
        adjusted = iter(_bh_adjust(raw_p))
        for row in pairwise_rows:
            row["p_adj"] = next(adjusted) if row["p"] is not None else None
    result["pairwise"] = pairwise_rows
    return result


def compute_stats_tests(figure: AnalysisFigure, rows: list) -> list | None:
    """stats_tests do §7.5 — só em figuras de stats (nunca distribution).

    Lista por população: cada entrada traz ``omnibus`` (ANOVA one-way ou
    Kruskal-Wallis), ``pairwise`` (Welch ou Mann-Whitney com correção BH
    em ``p_adj``), ``n_per_group`` e ``warnings``. Devolve None quando o
    chart_type não comporta teste — a chave fica ausente do cache.
    """
    if figure.chart_type not in _STATS_CHART_TYPES:
        return None
    spec = figure.spec or {}
    preference = spec.get("stats_test") or "auto"
    groups = spec.get("groups") or []
    return [
        _stats_tests_for_population(rows, pop, groups, preference)
        for pop in spec.get("populations") or []
    ]


def head_revision(figure: AnalysisFigure) -> AnalysisRevision | None:
    """Última revisão do experimento — âncora de procedência do cômputo."""
    return (
        AnalysisRevision.objects.filter(experiment=figure.experiment)
        .order_by("-id")
        .first()
    )


def figure_is_stale(figure: AnalysisFigure) -> bool:
    """True quando o estado da análise mudou sob os alvos da figura.

    Intersecta revisões posteriores ao ``result_revision`` com o
    fingerprint (gate/file ids resolvidos); ações experiment-wide
    marcam sempre. Sem cache/resolvido ainda → stale (nunca computada
    com base em dados).
    """
    cache = figure.result_cache or {}
    if not cache:
        return True  # nunca computada

    meta = cache.get("meta") or {}
    # Spec editado depois do último cômputo → stale (o PATCH não regrava
    # cache mudo — a resposta sinaliza, front oferece Recomputar).
    cached_fp = meta.get("spec_fingerprint")
    if cached_fp is not None and cached_fp != spec_fingerprint(figure.spec):
        return True

    resolved = cache.get("resolved_inputs") or {}
    gate_ids = set(resolved.get("gate_ids") or [])
    file_ids = set(resolved.get("file_data_ids") or [])

    revisions = AnalysisRevision.objects.filter(experiment=figure.experiment)
    if figure.result_revision_id:
        revisions = revisions.filter(id__gt=figure.result_revision_id)
    if not revisions.exists():
        return False

    if revisions.filter(action__in=EXPERIMENT_WIDE_ACTIONS).exists():
        return True
    if not gate_ids and not file_ids:
        # figura nunca resolveu nada — revisões existem mas nada a
        # comparar; só ação experiment-wide (já checada) marca.
        return False

    for rev in revisions.only("target_type", "target_id", "affected_ids").iterator():
        touched = {rev.target_id} | set(rev.affected_ids or [])
        if rev.target_type == AnalysisRevision.TARGET_GATE and touched & gate_ids:
            return True
        if rev.target_type == AnalysisRevision.TARGET_FILE and touched & file_ids:
            return True
    return False


def removed_since_last(before: dict | None, after: dict) -> dict:
    """Diff de resolução entre o cache anterior e o novo — o que estava
    resolvido e deixou de estar (gate renomeado, amostra arquivada)."""
    if not before:
        return {"populations": [], "files": []}

    def resolved_pairs(cache):
        pairs = set()
        for row in cache.get("resolved_pairs") or []:
            pairs.add((row.get("population"), row.get("file_data_id")))
        # Caches antigos sem resolved_pairs: deriva dos rows.
        for row in cache.get("rows") or []:
            pairs.add((row.get("population"), row.get("file_data_id")))
        return pairs

    old_pairs = resolved_pairs(before)
    new_pairs = resolved_pairs(after)
    removed = old_pairs - new_pairs

    file_names = {
        row.get("file_data_id"): row.get("file_name")
        for row in before.get("rows") or []
    }
    removed_files = [
        {"file_data_id": fd_id, "file_name": file_names.get(fd_id)}
        for fd_id in {p[1] for p in removed if p[1] is not None}
    ]

    removed_pops = sorted({p[0] for p in removed if p[0]})
    return {
        "populations": removed_pops,
        "files": removed_files,
    }
