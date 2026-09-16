import json
import logging
from collections import deque

import pandas as pd
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from fcs_parser.serializers import ParamListDataSerializer
from rest_framework import generics, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView, Response, status
from fcs_parser.permissions import (
    file_data_visible_to,
    require_can_edit_file_data,
)
from fcs_parser.services.compensation import (
    applied_compensation,
    apply_compensation,
)
from analytics.permissions import gates_visible_to, require_can_edit_gate
from analytics.gate_scope import (
    PROPAGATING_SCOPES,
    SCOPE_EXPERIMENT,
    effective_scope,
    gates_in_experiment_scope,
)
from analytics.history import (
    apply_restore,
    apply_revert,
    gate_snapshot,
    gate_subtree_snapshots,
    group_into_sessions,
    plan_revert,
    plan_restore,
    public_changes,
    record_revision,
    state_at_revision,
)
from analytics.models import (
    AnalysisCheckpoint,
    AnalysisRevision,
    DashboardModel,
    GateModel,
)
from analytics.serializers import (
    AnalysisCheckpointSerializer,
    AnalysisRevisionDetailSerializer,
    AnalysisRevisionSerializer,
    CheckpointCreateSerializer,
    CheckpointPatchSerializer,
    DashboardSerializer,
    GateBatchDeleteSerializer,
    GateSerializer,
    GateUpdateSerializer,
    RestoreSerializer,
    RevertRevisionSerializer,
)
from utils.density import (
    DEFAULT_COFACTOR,
    apply_gate_filter,
    compute_density,
    compute_histogram,
    default_scale,
    density_cache_key,
    empty_density_result,
    file_data_channels,
    get_cached_density,
    missing_gate_channels,
    normalize_column_name,
    normalize_columns,
    parse_range,
    set_cached_density,
    subsample_scatter,
)

logger = logging.getLogger(__name__)


def _propagate_gate_changes(
    gate,
    new_name,
    new_color,
    color_changed,
    new_coords=None,
    scope=SCOPE_EXPERIMENT,
    dry_run=False,
):
    """Aplica nome/cor/geometria do *gate* nas cópias dele nas outras amostras.

    Devolve (ids_propagados, conflitos, afetadas). Uma cópia entra em
    `conflitos` quando o novo nome já existe no mesmo nível da amostra de
    destino — as constraints `unique_gate_name_per_parent`/
    `unique_gate_name_root_level` impedem a renomeação, e as demais amostras
    seguem sendo atualizadas. Com `dry_run` nada é gravado: o retorno é o que
    *seria* alterado, para a UI confirmar antes.
    """
    propagated = []
    conflicts = []
    affected = []

    for copy in gates_in_experiment_scope(gate, scope=scope).select_related(
        "file_data"
    ):
        fields = []
        before_fields = {}
        if new_name is not None and copy.name != new_name:
            clash = (
                GateModel.objects.filter(
                    file_data_id=copy.file_data_id,
                    parent_id=copy.parent_id,
                    name=new_name,
                )
                .exclude(id=copy.id)
                .exists()
            )
            if clash:
                conflicts.append(
                    {
                        "gate_id": copy.id,
                        "file_data_id": copy.file_data_id,
                        "file_name": copy.file_data.file_name,
                        "detail": "Já existe um gate com esse nome neste nível.",
                    }
                )
            else:
                before_fields["name"] = copy.name
                copy.name = new_name
                fields.append("name")

        if color_changed:
            normalized = new_color if new_color else None
            if copy.color != normalized:
                before_fields["color"] = copy.color
                copy.color = normalized
                fields.append("color")

        if new_coords is not None and copy.gate_coordinates != new_coords:
            before_fields["gate_coordinates"] = copy.gate_coordinates
            copy.gate_coordinates = new_coords
            fields.append("gate_coordinates")

        if fields:
            if not dry_run:
                copy.save(update_fields=fields)
            propagated.append(copy.id)
            affected.append(
                {
                    "gate_id": copy.id,
                    "file_data_id": copy.file_data_id,
                    "file_name": copy.file_data.file_name,
                    "source_path": copy.file_data.source_path,
                    "fields": fields,
                    "before": before_fields,
                }
            )

    return propagated, conflicts, affected


class CreateGateView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = GateSerializer

    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        dashboard_data = data.pop("dashboard", None)

        dashboard_serializer = DashboardSerializer(data=dashboard_data)
        dashboard_serializer.is_valid(raise_exception=True)
        # O dashboard ancora o gate numa amostra — escrita exige can_edit
        # no experimento dela.
        require_can_edit_file_data(
            request.user, dashboard_serializer.validated_data["file_data"]
        )

        dash_instance = dashboard_serializer.save()
        data["dashboard"] = dash_instance.id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        gate_file_data = serializer.validated_data.get("file_data")
        if gate_file_data is not None:
            require_can_edit_file_data(request.user, gate_file_data)
        gate_instance = serializer.save(created_by=request.user)

        from analytics.tasks import recalculate_gate_analysis

        recalculate_gate_analysis(gate_instance.id)

        record_revision(
            experiment=gate_instance.file_data.experiment,
            action=AnalysisRevision.ACTION_CREATE,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=gate_instance.id,
            user=request.user,
            payload_after={
                "gates": {str(gate_instance.id): gate_snapshot(gate_instance)}
            },
            affected_ids=[gate_instance.id],
            summary=(
                f'criou o gate "{gate_instance.name}" em '
                f"{gate_instance.file_data.file_name}"
            ),
        )

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UpdateGateView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH/DELETE /analytics/gate/<gate_id> — rename or delete a gate."""

    permission_classes = [IsAuthenticated]
    serializer_class = GateSerializer
    lookup_url_kwarg = "gate_id"

    def get_object(self):
        gate_id = self.kwargs.get(self.lookup_url_kwarg)
        return get_object_or_404(gates_visible_to(self.request.user), pk=gate_id)

    def perform_destroy(self, instance):
        require_can_edit_gate(self.request.user, instance)
        snapshots = gate_subtree_snapshots(instance)
        record_revision(
            experiment=instance.file_data.experiment,
            action=AnalysisRevision.ACTION_DELETE,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=instance.id,
            user=self.request.user,
            payload_before={"gates": snapshots},
            affected_ids=[int(gid) for gid in snapshots],
            summary=(
                f'excluiu o gate "{instance.name}" em '
                f"{instance.file_data.file_name}"
                + (
                    f" ({len(snapshots) - 1} sub-gate(s) junto)"
                    if len(snapshots) > 1
                    else ""
                )
            ),
        )
        instance.delete()

    @extend_schema(request=GateUpdateSerializer, responses=GateSerializer)
    def patch(self, request, *args, **kwargs):
        gate = self.get_object()
        require_can_edit_gate(request.user, gate)
        payload = GateUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        scope = effective_scope(data["scope"], gate.file_data)

        update_fields = []
        gate_before = {}
        gate_after = {}
        new_name = data.get("name")
        if new_name is not None:
            if gate.name != new_name:
                gate_before["name"] = gate.name
                gate_after["name"] = new_name
            gate.name = new_name
            update_fields.append("name")
        new_coords = data.get("gate_coordinates")
        if new_coords is not None:
            if gate.gate_coordinates != new_coords:
                gate_before["gate_coordinates"] = gate.gate_coordinates
                gate_after["gate_coordinates"] = new_coords
            gate.gate_coordinates = new_coords
            update_fields.append("gate_coordinates")
            # Geometria customizada só nesta amostra desfaz o vínculo com a
            # família de cópias: a partir daqui o gate é próprio da amostra e
            # não acompanha mais as operações em escopo de experimento. No
            # escopo do experimento a mudança vale para a família inteira e o
            # vínculo é mantido.
            if gate.copied_from_id and scope not in PROPAGATING_SCOPES:
                gate_before["copied_from_id"] = gate.copied_from_id
                gate_after["copied_from_id"] = None
                gate.copied_from = None
                update_fields.append("copied_from")
        new_color = data.get("color")
        if new_color is not None:
            normalized_color = new_color if new_color else None
            if gate.color != normalized_color:
                gate_before["color"] = gate.color
                gate_after["color"] = normalized_color
            gate.color = normalized_color
            update_fields.append("color")
        new_plot_config = data.get("plot_config")
        if new_plot_config is not None:
            if gate.plot_config != new_plot_config:
                gate_before["plot_config"] = gate.plot_config
                gate_after["plot_config"] = new_plot_config
            gate.plot_config = new_plot_config
            update_fields.append("plot_config")

        if data["dry_run"]:
            affected = []
            conflicts = []
            if scope in PROPAGATING_SCOPES:
                _, conflicts, affected = _propagate_gate_changes(
                    gate,
                    new_name=new_name,
                    new_color=new_color,
                    color_changed="color" in data,
                    new_coords=new_coords,
                    scope=scope,
                    dry_run=True,
                )
            return Response(
                {
                    "dry_run": True,
                    "applied_scope": scope,
                    "affected": affected,
                    "conflicts": conflicts,
                },
                status=status.HTTP_200_OK,
            )

        propagated_ids = []
        conflicts = []
        propagated_affected = []
        with transaction.atomic():
            if update_fields:
                try:
                    gate.save(update_fields=update_fields)
                except IntegrityError:
                    return Response(
                        {
                            "detail": (
                                "Já existe um gate com esse nome neste nível da "
                                "amostra."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

            if scope in PROPAGATING_SCOPES:
                propagated_ids, conflicts, propagated_affected = (
                    _propagate_gate_changes(
                        gate,
                        new_name=new_name,
                        new_color=new_color,
                        color_changed="color" in data,
                        new_coords=new_coords,
                        scope=scope,
                    )
                )

            if gate_before or propagated_affected:
                new_values = {
                    "name": new_name,
                    "color": (new_color or None) if "color" in data else None,
                    "gate_coordinates": new_coords,
                }
                payload_before = {"gates": {}}
                payload_after = {"gates": {}}
                if gate_before:
                    payload_before["gates"][str(gate.id)] = gate_before
                    payload_after["gates"][str(gate.id)] = gate_after
                for entry in propagated_affected:
                    gid = str(entry["gate_id"])
                    payload_before["gates"][gid] = entry["before"]
                    payload_after["gates"][gid] = {
                        field: new_values[field] for field in entry["fields"]
                    }
                if new_coords is not None:
                    action = AnalysisRevision.ACTION_UPDATE_GEOMETRY
                    action_summary = f'alterou a geometria de "{gate.name}"'
                elif new_name is not None:
                    action = AnalysisRevision.ACTION_RENAME
                    old_name = gate_before.get("name", gate.name)
                    action_summary = f'renomeou "{old_name}" → "{new_name}"'
                else:
                    action = AnalysisRevision.ACTION_RECOLOR
                    action_summary = f'mudou a cor de "{gate.name}"'
                total = len(payload_before["gates"])
                record_revision(
                    experiment=gate.file_data.experiment,
                    action=action,
                    target_type=AnalysisRevision.TARGET_GATE,
                    target_id=gate.id,
                    user=request.user,
                    scope=scope,
                    payload_before=payload_before,
                    payload_after=payload_after,
                    affected_ids=[int(gid) for gid in payload_before["gates"]],
                    summary=action_summary
                    + (f" em {total} amostras (escopo {scope})" if total > 1 else ""),
                )

        # Só recalcula métricas/invalida densidade quando a geometria muda.
        # Alterações de nome/cor/plot_config não afetam a análise.
        if new_coords is not None:
            from analytics.tasks import recalculate_gate_analysis
            from utils.density import invalidate_density

            recalculate_gate_analysis(gate.id)
            invalidate_density(gate.file_data_id)
            for copy in GateModel.objects.filter(id__in=propagated_ids):
                recalculate_gate_analysis(copy.id)
                invalidate_density(copy.file_data_id)

        serializer = self.get_serializer(gate)
        return Response(
            {
                **serializer.data,
                "propagated_gate_ids": propagated_ids,
                "conflicts": conflicts,
                "applied_scope": scope,
            },
            status=status.HTTP_200_OK,
        )


class GetGateDataView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = GateSerializer
    lookup_url_kwarg = "gate_id"

    def get_object(self):
        gate_id = self.kwargs.get(self.lookup_url_kwarg)
        return get_object_or_404(gates_visible_to(self.request.user), pk=gate_id)

    def _apply_gate_filter(
        self, dataset: pd.DataFrame, gate: GateModel
    ) -> pd.DataFrame:
        """Aplica o filtro de um gate (retangulo ou poligono) ao dataset.

        Delega ao helper compartilhado (utils.density.apply_gate_filter), que
        trata retangulo e poligono de forma vetorizada.
        """
        return apply_gate_filter(dataset, gate)

    def get(self, request, *args, **kwargs):

        target_gate = self.get_object()

        current_gate = target_gate
        gate_path = [current_gate]

        while current_gate.parent:
            current_gate = current_gate.parent
            gate_path.insert(0, current_gate)

        root_gate = gate_path[0]
        file_data_instance = root_gate.file_data

        dataset = file_data_instance.get_dataframe()

        dataset = normalize_columns(dataset)
        # BE-22: gates avaliam no espaço exibido — com compensação aplicada,
        # os eventos já vêm multiplicados por S⁻¹.
        applied = applied_compensation(file_data_instance.experiment)
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)
        columns = set(dataset.columns)

        for gate_in_path in gate_path:
            # Canal ausente invalida o gate e a linhagem abaixo dele (ADR-0016).
            missing = missing_gate_channels(gate_in_path, columns)
            if missing:
                return Response(
                    {
                        "detail": (
                            f"O gate '{gate_in_path.name}' referencia o(s) "
                            f"canal(is) {', '.join(missing)}, ausente(s) nesta "
                            "amostra."
                        ),
                        "missing_channels": missing,
                        "gate_id": gate_in_path.id,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            dataset = self._apply_gate_filter(dataset, gate_in_path)
            if dataset.empty:
                break
        limit = request.query_params.get("limit", 10000)
        try:
            limit = int(limit)
        except ValueError:
            limit = 10000
        dataset = dataset.head(limit)
        file_data_instance.data_set = json.loads(dataset.to_json(orient="records"))
        serializer = ParamListDataSerializer(file_data_instance)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GateDensityView(APIView):
    """Return density (heatmap) or subsampled scatter for a gate's filtered data."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="x",
                type=str,
                required=True,
                description="X-axis parameter (e.g. FSC-A)",
            ),
            OpenApiParameter(
                name="y",
                type=str,
                required=True,
                description="Y-axis parameter (e.g. SSC-A)",
            ),
            OpenApiParameter(
                name="mode",
                type=str,
                required=False,
                description="'heatmap' (default) or 'scatter'",
            ),
            OpenApiParameter(
                name="bins",
                type=int,
                required=False,
                description="Bins for heatmap (default 200)",
            ),
            OpenApiParameter(
                name="sample",
                type=int,
                required=False,
                description="Max points for scatter (default 5000)",
            ),
            OpenApiParameter(
                name="xscale",
                type=str,
                required=False,
                description="'linear' or 'biex' (default: heuristic by channel)",
            ),
            OpenApiParameter(
                name="yscale",
                type=str,
                required=False,
                description="'linear' or 'biex' (default: heuristic by channel)",
            ),
            OpenApiParameter(
                name="cofactor",
                type=float,
                required=False,
                description="arcsinh cofactor for biex (default 150)",
            ),
            OpenApiParameter(
                name="cutoff",
                type=int,
                required=False,
                description="Heatmap density cutoff: bins with count <= cutoff become null/transparent (default 0)",
            ),
            OpenApiParameter(
                name="xmin",
                type=float,
                required=False,
                description="Lower bound for X axis (raw value)",
            ),
            OpenApiParameter(
                name="xmax",
                type=float,
                required=False,
                description="Upper bound for X axis (raw value)",
            ),
            OpenApiParameter(
                name="ymin",
                type=float,
                required=False,
                description="Lower bound for Y axis (raw value)",
            ),
            OpenApiParameter(
                name="ymax",
                type=float,
                required=False,
                description="Upper bound for Y axis (raw value)",
            ),
        ],
        responses=inline_serializer(
            name="GateDensityResponse",
            fields={
                "mode": serializers.CharField(),
                "total_events": serializers.IntegerField(),
                "x_label": serializers.CharField(),
                "y_label": serializers.CharField(),
            },
        ),
    )
    def get(self, request, gate_id):
        x_param = request.query_params.get("x", "FSC-A")
        y_param = request.query_params.get("y", "SSC-A")
        mode = request.query_params.get("mode", "heatmap")
        bins = int(request.query_params.get("bins", 200))
        sample = int(request.query_params.get("sample", 5000))
        x_scale = request.query_params.get("xscale") or default_scale(x_param)
        y_scale = request.query_params.get("yscale") or default_scale(y_param)
        cofactor = float(request.query_params.get("cofactor", DEFAULT_COFACTOR))
        try:
            cutoff = max(int(request.query_params.get("cutoff", 0)), 0)
        except (TypeError, ValueError):
            cutoff = 0
        x_range = parse_range(request.query_params, "xmin", "xmax")
        y_range = parse_range(request.query_params, "ymin", "ymax")

        gate = get_object_or_404(gates_visible_to(request.user), pk=gate_id)

        # BE-22: a matriz aplicada entra na chave de cache.
        applied = applied_compensation(gate.file_data.experiment)

        cache_key = density_cache_key(
            "gate",
            gate.file_data_id,
            gate_id,
            x_param,
            y_param,
            mode,
            bins,
            sample,
            x_scale,
            y_scale,
            cofactor,
            cutoff,
        )
        if x_range:
            cache_key += f":xr{x_range[0]}:{x_range[1]}"
        if y_range:
            cache_key += f":yr{y_range[0]}:{y_range[1]}"
        cache_key += f":comp{applied.id if applied else 0}"
        cached = get_cached_density(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        current = gate
        gate_path = [current]
        while current.parent:
            current = current.parent
            gate_path.insert(0, current)

        file_data = gate_path[0].file_data
        dataset = normalize_columns(file_data.get_dataframe())
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)
        columns = set(dataset.columns)

        for g in gate_path:
            # Canal ausente invalida o gate e a linhagem abaixo dele (ADR-0016):
            # é erro explicável, não falha genérica — o front mostra qual canal
            # falta em vez de "Erro ao carregar dados".
            missing = missing_gate_channels(g, columns)
            if missing:
                return Response(
                    {
                        "detail": (
                            f"O gate '{g.name}' referencia o(s) canal(is) "
                            f"{', '.join(missing)}, ausente(s) nesta amostra."
                        ),
                        "missing_channels": missing,
                        "gate_id": g.id,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            dataset = apply_gate_filter(dataset, g)
            if dataset.empty:
                break

        requested = [x_param] if mode == "histogram" else [x_param, y_param]
        missing_axes = [p for p in requested if normalize_column_name(p) not in columns]
        if missing_axes:
            return Response(
                {
                    "detail": (
                        f"Canal(is) não encontrado(s) nesta amostra: "
                        f"{', '.join(missing_axes)}."
                    ),
                    "missing_channels": missing_axes,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        base = {
            "mode": mode,
            "total_events": len(dataset),
            "x_label": x_param,
            "y_label": y_param,
        }

        if mode == "scatter":
            result = subsample_scatter(
                dataset,
                x_param,
                y_param,
                sample,
                x_scale,
                y_scale,
                cofactor,
                x_range,
                y_range,
            )
        elif mode == "histogram":
            result = compute_histogram(
                dataset, x_param, bins, x_scale, cofactor, x_range
            )
        else:
            result = compute_density(
                dataset,
                x_param,
                y_param,
                bins,
                x_scale,
                y_scale,
                cofactor,
                cutoff,
                x_range,
                y_range,
            )

        if result is None:
            # Colunas existem mas não há evento válido: gate legítimo que
            # filtrou tudo — resposta vazia do modo, não erro.
            result = empty_density_result(mode, x_scale, y_scale, cofactor, cutoff)

        payload = {**base, **result}
        set_cached_density(cache_key, payload)
        return Response(payload, status=status.HTTP_200_OK)


def _resolve_target_parent(source_gate, target_fd_id, id_map):
    """Resolve the parent for *source_gate* inside the target file.

    If the source gate's parent was already created in the target (present in
    *id_map*), return that id.  Otherwise walk up the source ancestry and try
    to match the same name hierarchy in the target — this ensures that when a
    user applies a sub-tree (e.g. CD3+ under Lymphocytes), the code finds the
    existing 'Lymphocytes' in the target and places the gate underneath it
    instead of creating a duplicate at root level.

    Returns ``None`` when no matching hierarchy exists (gate becomes root).
    """
    if source_gate.parent_id is None:
        return None

    if source_gate.parent_id in id_map:
        return id_map[source_gate.parent_id]

    # Build the ancestry path (root-first) for the unmapped parent chain.
    ancestry = []
    current = source_gate.parent
    while current and current.id not in id_map:
        ancestry.insert(0, current)
        current = current.parent

    # If we reached a gate already in id_map, start from there.
    target_parent_id = id_map.get(current.id) if current else None

    for ancestor in ancestry:
        match = GateModel.objects.filter(
            file_data_id=target_fd_id,
            name=ancestor.name,
            parent_id=target_parent_id,
        ).first()
        if match is None:
            return target_parent_id  # partial match; attach here
        target_parent_id = match.id

    return target_parent_id


class DeleteGateBatchView(APIView):
    """POST /analytics/gate/delete-batch — exclui gates com escopo explícito.

    Operação inversa do `ApplyGateView`: remove o gate só na amostra atual
    (`scope="file"`, default), as cópias dele nas demais amostras do mesmo
    experimento (`scope="experiment"`) ou apenas as do mesmo subsample
    (`scope="subsample"`). Nunca atinge outros experimentos nem amostras
    desabilitadas.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=GateBatchDeleteSerializer,
        responses=inline_serializer(
            name="DeleteGateBatchResponse",
            fields={
                "deleted": serializers.IntegerField(),
                "details": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    def post(self, request):
        payload = GateBatchDeleteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        source_gates = list(
            gates_visible_to(request.user)
            .filter(id__in=data["source_gate_ids"])
            .select_related("file_data", "file_data__experiment")
        )
        if len(source_gates) != len(set(data["source_gate_ids"])):
            return Response(
                {"detail": "Um ou mais gates não foram encontrados."},
                status=status.HTTP_404_NOT_FOUND,
            )

        for gate in source_gates:
            require_can_edit_gate(request.user, gate)

        targets = {}
        for gate in source_gates:
            scope = effective_scope(data["scope"], gate.file_data)
            if scope in PROPAGATING_SCOPES:
                scoped = gates_in_experiment_scope(
                    gate,
                    target_file_data_ids=data["target_file_data_ids"],
                    include_source=data["include_source"],
                    scope=scope,
                )
                for copy in scoped:
                    targets[copy.id] = copy
                if data["include_source"]:
                    targets[gate.id] = gate
            else:
                targets[gate.id] = gate

        if not targets:
            return Response(
                {"deleted": 0, "details": []},
                status=status.HTTP_200_OK,
            )

        # A FK `parent` é CASCADE: apagar um gate leva os sub-gates junto. Sem
        # `recursive` a operação é recusada para o usuário não perder a árvore
        # abaixo sem ter escolhido isso.
        if not data["recursive"]:
            with_children = GateModel.objects.filter(
                parent_id__in=list(targets.keys())
            ).exclude(id__in=list(targets.keys()))
            if with_children.exists():
                return Response(
                    {
                        "detail": (
                            "Os gates selecionados possuem sub-gates. Reenvie com "
                            '"recursive": true para excluir a árvore inteira.'
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        per_file = {}
        for gate in targets.values():
            per_file[gate.file_data_id] = per_file.get(gate.file_data_id, 0) + 1

        # BE-08: snapshot de cada alvo + subárvore antes do delete — a CASCADE
        # de `parent` leva os filhos junto e é o que viabiliza o revert.
        snapshots = {}
        for gate in targets.values():
            for gid, snap in gate_subtree_snapshots(gate).items():
                snapshots.setdefault(gid, snap)

        with transaction.atomic():
            deleted, _ = GateModel.objects.filter(id__in=list(targets.keys())).delete()
            record_revision(
                experiment=source_gates[0].file_data.experiment,
                action=AnalysisRevision.ACTION_DELETE,
                target_type=AnalysisRevision.TARGET_GATE,
                target_id=source_gates[0].id,
                user=request.user,
                scope=data["scope"],
                payload_before={"gates": snapshots},
                affected_ids=[int(gid) for gid in snapshots],
                summary=(
                    f"excluiu {deleted} gate(s)"
                    + (f" (escopo {data['scope']})" if data["scope"] != "file" else "")
                ),
            )

        from utils.density import invalidate_density

        for file_data_id in per_file:
            invalidate_density(file_data_id)

        return Response(
            {
                "deleted": deleted,
                "details": [
                    {"file_data_id": fd_id, "gates_deleted": count}
                    for fd_id, count in per_file.items()
                ],
            },
            status=status.HTTP_200_OK,
        )


def _apply_conflicts(ordered_gates, target_file_data_ids):
    """Gates de destino que seriam sobrescritos/renomeados pela aplicação.

    Só considera os gates cujo pai já existe no destino (id_map vazio): os
    sub-gates criados durante a aplicação não podem colidir com nada.
    """
    found = []
    for target_fd_id in target_file_data_ids:
        for gate in ordered_gates:
            parent_id = _resolve_target_parent(gate, target_fd_id, {})
            existing = GateModel.objects.filter(
                file_data_id=target_fd_id,
                name=gate.name,
                parent_id=parent_id,
            ).first()
            if existing:
                found.append(
                    {
                        "gate_id": existing.id,
                        "file_data_id": target_fd_id,
                        "file_name": existing.file_data.file_name,
                        "name": existing.name,
                    }
                )
    return found


class ApplyGateView(APIView):
    """POST /analytics/gate/apply — copy gates to other files (FlowJo semantics).

    Request body:
    {
      "source_gate_ids": [42],
      "target_file_data_ids": [10, 11],
      "recursive": true,           // include sub-gates (default true)
      "on_conflict": "rename",     // "rename" | "replace" | "skip"
      "dry_run": false             // só lista os conflitos, não grava nada
    }

    Em `on_conflict="replace"` o gate de destino é sobrescrito no lugar
    (geometria, cor, `plot_config` e vínculo com o original), preservando id e
    sub-gates existentes.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="ApplyGateRequest",
            fields={
                "source_gate_ids": serializers.ListField(
                    child=serializers.IntegerField()
                ),
                "target_file_data_ids": serializers.ListField(
                    child=serializers.IntegerField()
                ),
                "recursive": serializers.BooleanField(default=True),
                "on_conflict": serializers.ChoiceField(
                    choices=["rename", "replace", "skip"], default="rename"
                ),
                "dry_run": serializers.BooleanField(default=False),
            },
        ),
        responses=inline_serializer(
            name="ApplyGateResponse",
            fields={
                "created": serializers.IntegerField(),
                "skipped": serializers.IntegerField(),
                "replaced": serializers.IntegerField(),
                "conflicts": serializers.ListField(child=serializers.DictField()),
                "non_evaluable": serializers.ListField(child=serializers.DictField()),
                "details": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    def post(self, request):
        source_ids = request.data.get("source_gate_ids", [])
        target_ids = request.data.get("target_file_data_ids", [])
        recursive = request.data.get("recursive", True)
        on_conflict = request.data.get("on_conflict", "replace")
        dry_run = bool(request.data.get("dry_run", False))
        author = request.user

        if not source_ids or not target_ids:
            return Response(
                {"detail": "source_gate_ids and target_file_data_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_gates = list(
            gates_visible_to(request.user)
            .filter(id__in=source_ids)
            .select_related(
                "dashboard",
                "parent",
                "parent__parent",
                "parent__parent__parent",
                "file_data__experiment",
            )
        )
        if len(source_gates) != len(source_ids):
            return Response(
                {"detail": "One or more source gates not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Aplicar exporta a estratégia de análise — exige can_edit na origem
        # (mesmo critério zero trust do ExperimentCopyView).
        for gate in source_gates:
            require_can_edit_gate(request.user, gate)

        # Exclude source file(s) from target list to prevent self-copy.
        source_file_ids = {g.file_data_id for g in source_gates}
        target_ids = [fid for fid in target_ids if fid not in source_file_ids]
        if not target_ids:
            return Response(
                {"detail": "No valid target files (source file excluded)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Destinos: só amostras visíveis e editáveis pelo usuário.
        target_files = {
            fd.id: fd
            for fd in file_data_visible_to(request.user)
            .filter(id__in=target_ids)
            .select_related("experiment")
        }
        if len(target_files) != len(set(target_ids)):
            return Response(
                {"detail": "One or more target files not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        for fd in target_files.values():
            require_can_edit_file_data(request.user, fd)

        # Auto-expand quadrant groups: if a quadrant gate is selected, include all 4 Qs.
        expanded = set(source_ids)
        for gate in source_gates:
            gc = gate.gate_coordinates
            if gc.get("type") == "quadrant" and gate.parent_id is not None:
                siblings = GateModel.objects.filter(
                    parent_id=gate.parent_id,
                    file_data_id=gate.file_data_id,
                ).exclude(id__in=expanded)
                for sib in siblings:
                    if sib.gate_coordinates.get("type") == "quadrant":
                        expanded.add(sib.id)
        if expanded != set(source_ids):
            source_gates = list(
                GateModel.objects.filter(id__in=expanded).select_related("dashboard")
            )

        # Collect the full tree via BFS if recursive.
        ordered_gates = []
        queue = deque(source_gates)
        while queue:
            g = queue.popleft()
            ordered_gates.append(g)
            if recursive:
                children = list(g.children.select_related("dashboard").all())
                queue.extend(children)

        from analytics.tasks import recalculate_gate_analysis
        from utils.density import invalidate_density

        # Amostras onde algum gate aplicado não pode ser avaliado porque o
        # canal não existe no arquivo (ADR-0016): a cópia é criada mesmo
        # assim, mas a UI avisa que a linhagem ficará marcada como
        # não-avaliável lá — presente no dry_run e na aplicação real.
        non_evaluable = []
        for target_fd_id in target_ids:
            cols = file_data_channels(target_files[target_fd_id])
            missing_map = {}
            for gate in ordered_gates:
                missing = missing_gate_channels(gate, cols)
                if missing:
                    missing_map[gate.id] = missing
            if missing_map:
                non_evaluable.append(
                    {
                        "file_data_id": target_fd_id,
                        "file_name": target_files[target_fd_id].file_name,
                        "missing_channels": sorted(
                            {c for ms in missing_map.values() for c in ms}
                        ),
                        "gate_ids": sorted(missing_map),
                    }
                )

        if dry_run:
            return Response(
                {
                    "created": 0,
                    "skipped": 0,
                    "replaced": 0,
                    "conflicts": _apply_conflicts(ordered_gates, target_ids),
                    "non_evaluable": non_evaluable,
                    "details": [],
                },
                status=status.HTTP_200_OK,
            )

        total_created = 0
        total_skipped = 0
        total_replaced = 0
        conflicts = []
        details = []
        created_gate_ids = []
        created_snapshots = {}
        replaced_before = {}
        replaced_after = {}

        with transaction.atomic():
            for target_fd_id in target_ids:
                id_map = {}  # source gate id → new gate id
                file_created = 0
                file_skipped = 0
                file_replaced = 0

                for gate in ordered_gates:
                    # Determine new parent in target file.
                    new_parent_id = _resolve_target_parent(gate, target_fd_id, id_map)

                    # Conflict check.
                    existing = GateModel.objects.filter(
                        file_data_id=target_fd_id,
                        name=gate.name,
                        parent_id=new_parent_id,
                    ).first()

                    gate_name = gate.name

                    if existing:
                        if on_conflict == "skip":
                            id_map[gate.id] = existing.id
                            file_skipped += 1
                            continue
                        elif on_conflict == "replace":
                            replaced_before[str(existing.id)] = gate_snapshot(existing)
                            existing.gate_coordinates = gate.gate_coordinates
                            existing.plot_config = gate.plot_config
                            existing.color = gate.color
                            existing.copied_from = gate
                            existing.save(
                                update_fields=[
                                    "gate_coordinates",
                                    "plot_config",
                                    "color",
                                    "copied_from",
                                ]
                            )
                            id_map[gate.id] = existing.id
                            file_replaced += 1
                            replaced_after[str(existing.id)] = gate_snapshot(existing)
                            conflicts.append(
                                {
                                    "gate_id": existing.id,
                                    "file_data_id": target_fd_id,
                                    "name": existing.name,
                                    "resolution": "replaced",
                                }
                            )
                            continue
                        else:  # rename
                            suffix = 2
                            gate_name = f"{gate.name} ({suffix})"
                            while GateModel.objects.filter(
                                file_data_id=target_fd_id,
                                name=gate_name,
                                parent_id=new_parent_id,
                            ).exists():
                                suffix += 1
                                gate_name = f"{gate.name} ({suffix})"

                    # Clone dashboard for the target file.
                    src_dash = gate.dashboard
                    dash_name = f"{src_dash.name}_fd{target_fd_id}_{gate_name}"
                    # Truncate to 50 chars (model max_length)
                    dash_name = dash_name[:50]
                    new_dash, _ = DashboardModel.objects.update_or_create(
                        name=dash_name,
                        file_data_id=target_fd_id,
                        defaults={"dashboard_config": src_dash.dashboard_config},
                    )

                    new_gate = GateModel.objects.create(
                        file_data_id=target_fd_id,
                        name=gate_name,
                        gate_coordinates=gate.gate_coordinates,
                        plot_config=gate.plot_config,
                        dashboard=new_dash,
                        parent_id=new_parent_id,
                        copied_from=gate,
                        color=gate.color,
                        created_by=author,
                    )
                    id_map[gate.id] = new_gate.id
                    file_created += 1
                    created_gate_ids.append(new_gate.id)
                    created_snapshots[str(new_gate.id)] = gate_snapshot(new_gate)

                details.append(
                    {
                        "file_data_id": target_fd_id,
                        "gates_created": file_created,
                        "gates_skipped": file_skipped,
                        "gates_replaced": file_replaced,
                    }
                )
                total_created += file_created
                total_skipped += file_skipped
                total_replaced += file_replaced

            record_revision(
                experiment=target_files[target_ids[0]].experiment,
                action=AnalysisRevision.ACTION_APPLY,
                target_type=AnalysisRevision.TARGET_GATE,
                target_id=source_gates[0].id,
                user=request.user,
                payload_before={"replaced": replaced_before},
                payload_after={
                    "created_gate_ids": created_gate_ids,
                    "created": created_snapshots,
                    "replaced": replaced_after,
                },
                affected_ids=created_gate_ids + [int(g) for g in replaced_after],
                summary=(
                    f"aplicou {len(source_gates)} gate(s) em "
                    f"{len(target_ids)} amostra(s) ({total_created} criados, "
                    f"{total_replaced} substituídos, {total_skipped} ignorados)"
                ),
            )

        # Trigger async recalculation + cache invalidation outside the transaction.
        for target_fd_id in target_ids:
            invalidate_density(target_fd_id)
        for detail in details:
            fd_id = detail["file_data_id"]
            root_gates = GateModel.objects.filter(
                file_data_id=fd_id,
                parent__isnull=True,
                copied_from__isnull=False,
            )
            for rg in root_gates:
                recalculate_gate_analysis(rg.id)

        return Response(
            {
                "created": total_created,
                "skipped": total_skipped,
                "replaced": total_replaced,
                "conflicts": conflicts,
                "non_evaluable": non_evaluable,
                "details": details,
            },
            status=status.HTTP_201_CREATED,
        )


class ExperimentHistoryView(generics.ListAPIView):
    """GET /analytics/experiment/<experiment_id>/history/

    Log append-only da análise (BE-08/ADR-0008), mais recente primeiro.
    Filtros: `target=gate:51` (ou subsample/file/experiment), `user=<id>`;
    paginação por cursor: `cursor=<revision_id>` devolve os itens
    anteriores a ele (50 por página).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AnalysisRevisionSerializer
    PAGE_SIZE = 50

    def get_queryset(self):
        from fcs_parser.permissions import experiments_visible_to

        experiment = get_object_or_404(
            experiments_visible_to(self.request.user),
            id=self.kwargs["experiment_id"],
        )
        qs = AnalysisRevision.objects.filter(experiment=experiment).select_related(
            "user"
        )
        target = self.request.query_params.get("target")
        if target and ":" in target:
            target_type, target_id = target.split(":", 1)
            if target_id.isdigit():
                qs = qs.filter(target_type=target_type, target_id=int(target_id))
        user_id = self.request.query_params.get("user")
        if user_id and user_id.isdigit():
            qs = qs.filter(user_id=int(user_id))
        # ?file=<file_data_id> recorta a timeline pelo que toca a amostra:
        # revisões da amostra + as experiment-wide (file_data NULL —
        # compensação, restore etc. afetam todas as amostras).
        file_id = self.request.query_params.get("file")
        if file_id and file_id.isdigit():
            qs = qs.filter(Q(file_data_id=int(file_id)) | Q(file_data__isnull=True))
        cursor = self.request.query_params.get("cursor")
        if cursor and cursor.isdigit():
            qs = qs.filter(id__lt=int(cursor))
        return qs.order_by("-id")

    def list(self, request, *args, **kwargs):
        page = list(self.get_queryset()[: self.PAGE_SIZE + 1])
        has_more = len(page) > self.PAGE_SIZE
        page = page[: self.PAGE_SIZE]
        serializer = self.get_serializer(page, many=True)
        if request.query_params.get("grouped"):
            checkpoints = {
                cp.revision_id: AnalysisCheckpointSerializer(cp).data
                for cp in AnalysisCheckpoint.objects.filter(
                    experiment_id=self.kwargs["experiment_id"], active=True
                )
            }
            sessions = group_into_sessions(page)
            idx = 0
            for s in sessions:
                s["checkpoint"] = checkpoints.get(s["end_revision_id"])
                s["revisions"] = serializer.data[idx : idx + s["count"]]  # noqa: E203
                idx += s["count"]
            return Response(
                {
                    "sessions": sessions,
                    "next_cursor": page[-1].id if has_more and page else None,
                }
            )
        return Response(
            {
                "results": serializer.data,
                "next_cursor": page[-1].id if has_more and page else None,
            }
        )


class HistoryDetailView(APIView):
    """GET /analytics/history/<revision_id>/ — revisão + o que reverteria."""

    permission_classes = [IsAuthenticated]

    def get(self, request, revision_id):
        from fcs_parser.permissions import experiments_visible_to

        revision = get_object_or_404(
            AnalysisRevision.objects.select_related("user", "experiment"),
            id=revision_id,
            experiment__in=experiments_visible_to(request.user),
        )
        data = AnalysisRevisionDetailSerializer(revision).data
        if data["revertible"]:
            data["revert_preview"] = plan_revert(revision)
        return Response(data)


class HistoryRevertView(APIView):
    """POST /analytics/history/<revision_id>/revert/ {"dry_run": bool}.

    Reverte uma revisão aplicando o inverso como uma revisão nova
    (`action="revert"`, `reverts=<id>`) — o log nunca é reescrito.
    `dry_run` devolve `would_change`/`conflicts` sem gravar; com conflitos
    a reversão é bloqueada, nunca aplicada parcialmente.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=RevertRevisionSerializer,
        responses=inline_serializer(
            name="RevertResponse",
            fields={
                "would_change": serializers.ListField(child=serializers.DictField()),
                "conflicts": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    def post(self, request, revision_id):
        from fcs_parser.permissions import (
            can_edit_experiment,
            experiments_visible_to,
        )

        revision = get_object_or_404(
            AnalysisRevision.objects.select_related("experiment"),
            id=revision_id,
            experiment__in=experiments_visible_to(request.user),
        )
        if not can_edit_experiment(request.user, revision.experiment):
            raise PermissionDenied(
                "Reverter exige permissão de escrita no experimento."
            )
        payload = RevertRevisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        plan = plan_revert(revision)
        if payload.validated_data["dry_run"] or plan["conflicts"]:
            return Response(
                {
                    "would_change": public_changes(plan),
                    "conflicts": plan["conflicts"],
                },
                status=(
                    status.HTTP_200_OK
                    if payload.validated_data["dry_run"]
                    else status.HTTP_409_CONFLICT
                ),
            )
        result = apply_revert(revision, request.user)
        return Response(
            {"would_change": public_changes(plan), "conflicts": []},
            status=status.HTTP_200_OK,
        )


class _ScopedExperimentMixin:
    """Resolve o experimento do path dentro do escopo de visão do usuário."""

    def get_experiment(self, request):
        from fcs_parser.permissions import experiments_visible_to

        return get_object_or_404(
            experiments_visible_to(request.user), id=self.kwargs["experiment_id"]
        )


class CheckpointListCreateView(_ScopedExperimentMixin, APIView):
    """GET/POST /analytics/experiment/<id>/checkpoints/ (BE-20).

    POST {"message"?, "revision_id"?} fixa um marco: sem `revision_id`,
    marca a última revisão do experimento; com, faz o "pin" de uma borda
    passada (auto-checkpoint ou revisão avulsa).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        experiment = self.get_experiment(request)
        qs = experiment.checkpoints.filter(active=True).order_by("-created_at")
        return Response({"results": AnalysisCheckpointSerializer(qs, many=True).data})

    @extend_schema(
        request=CheckpointCreateSerializer,
        responses=AnalysisCheckpointSerializer,
    )
    def post(self, request, experiment_id):
        from fcs_parser.permissions import can_edit_experiment

        experiment = self.get_experiment(request)
        if not can_edit_experiment(request.user, experiment):
            raise PermissionDenied("Criar checkpoint exige permissão de escrita.")
        payload = CheckpointCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        revision_id = payload.validated_data.get("revision_id")
        if revision_id is not None:
            revision = get_object_or_404(
                AnalysisRevision, id=revision_id, experiment=experiment
            )
        else:
            revision = experiment.analysis_revisions.order_by("-id").first()
        checkpoint = AnalysisCheckpoint.objects.create(
            experiment=experiment,
            revision=revision,
            message=payload.validated_data.get("message", ""),
            created_by=request.user,
        )
        return Response(
            AnalysisCheckpointSerializer(checkpoint).data,
            status=status.HTTP_201_CREATED,
        )


class CheckpointDetailView(APIView):
    """PATCH/DELETE /analytics/checkpoints/<id>/ — renomear/descartar (soft)."""

    permission_classes = [IsAuthenticated]

    def _get_checkpoint(self, request, pk):
        from fcs_parser.permissions import experiments_visible_to

        return get_object_or_404(
            AnalysisCheckpoint.objects.select_related("experiment"),
            id=pk,
            experiment__in=experiments_visible_to(request.user),
        )

    @extend_schema(
        request=CheckpointPatchSerializer,
        responses=AnalysisCheckpointSerializer,
    )
    def patch(self, request, pk):
        from fcs_parser.permissions import can_edit_experiment

        checkpoint = self._get_checkpoint(request, pk)
        if not can_edit_experiment(request.user, checkpoint.experiment):
            raise PermissionDenied("Editar checkpoint exige permissão de escrita.")
        payload = CheckpointPatchSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        checkpoint.message = payload.validated_data["message"]
        checkpoint.save(update_fields=["message"])
        return Response(AnalysisCheckpointSerializer(checkpoint).data)

    def delete(self, request, pk):
        from fcs_parser.permissions import can_edit_experiment

        checkpoint = self._get_checkpoint(request, pk)
        if not can_edit_experiment(request.user, checkpoint.experiment):
            raise PermissionDenied("Descartar checkpoint exige permissão de escrita.")
        checkpoint.active = False
        checkpoint.save(update_fields=["active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


def _restore_response(experiment, target_revision, request):
    """Fluxo comum de restore: dry_run → plano; real → aplica ou bloqueia."""
    payload = RestoreSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    force = payload.validated_data["force"]
    if payload.validated_data["dry_run"]:
        outcome = plan_restore(experiment, target_revision, force=force)
        return Response(
            {
                "would_change": [
                    {
                        "revision_id": i["revision"].id,
                        "changes": public_changes(i["plan"]),
                    }
                    for i in outcome["plans"]
                ],
                "conflicts": outcome["conflicts"],
            }
        )
    result = apply_restore(experiment, target_revision, request.user, force=force)
    if result["blocked"]:
        return Response(
            {
                "would_change": result["would_change"],
                "conflicts": result["conflicts"],
            },
            status=status.HTTP_409_CONFLICT,
        )
    return Response(
        {"applied": result["applied"], "skipped": result["skipped"]},
        status=status.HTTP_200_OK,
    )


class HistoryRestoreView(_ScopedExperimentMixin, APIView):
    """POST /analytics/experiment/<id>/history/<rev>/restore/ {"dry_run","force"}.

    Desfaz em cadeia tudo que veio depois da revisão-alvo (BE-20/ADR-0017).
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=RestoreSerializer)
    def post(self, request, experiment_id, revision_id):
        from fcs_parser.permissions import can_edit_experiment

        experiment = self.get_experiment(request)
        if not can_edit_experiment(request.user, experiment):
            raise PermissionDenied("Restaurar exige permissão de escrita.")
        target = get_object_or_404(
            AnalysisRevision, id=revision_id, experiment=experiment
        )
        return _restore_response(experiment, target, request)


class CheckpointRestoreView(APIView):
    """POST /analytics/checkpoints/<id>/restore/ — mesmo motor, alvo = marco."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=RestoreSerializer)
    def post(self, request, pk):
        from fcs_parser.permissions import (
            can_edit_experiment,
            experiments_visible_to,
        )

        checkpoint = get_object_or_404(
            AnalysisCheckpoint.objects.select_related("experiment"),
            id=pk,
            experiment__in=experiments_visible_to(request.user),
            active=True,
        )
        if not can_edit_experiment(request.user, checkpoint.experiment):
            raise PermissionDenied("Restaurar exige permissão de escrita.")
        return _restore_response(checkpoint.experiment, checkpoint.revision, request)


class HistoryStateView(APIView):
    """GET /analytics/history/<revision_id>/state/ — árvore naquela revisão.

    Preview read-only do FE-25: reconstrói a floresta de gates aplicando os
    inversos das revisões posteriores ao alvo, sem gravar nada.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, revision_id):
        from fcs_parser.permissions import experiments_visible_to

        revision = get_object_or_404(
            AnalysisRevision.objects.select_related("experiment"),
            id=revision_id,
            experiment__in=experiments_visible_to(request.user),
        )
        return Response(state_at_revision(revision.experiment, revision))


class CompensationDetailView(APIView):
    """PATCH/DELETE /analytics/compensations/<id>/ — renomear/descartar (BE-22).

    DELETE é soft delete (active=false). Se a matriz estiver aplicada,
    desliga primeiro — equivale a um remove (revisão + recálculo).
    """

    permission_classes = [IsAuthenticated]

    def _get_matrix(self, request, pk):
        from analytics.models import CompensationMatrix
        from fcs_parser.permissions import experiments_visible_to

        return get_object_or_404(
            CompensationMatrix.objects.select_related("experiment"),
            id=pk,
            experiment__in=experiments_visible_to(request.user),
        )

    def patch(self, request, pk):
        from analytics.serializers import CompensationMatrixSerializer
        from fcs_parser.permissions import can_edit_experiment

        matrix = self._get_matrix(request, pk)
        if not can_edit_experiment(request.user, matrix.experiment):
            raise PermissionDenied("Editar compensação exige permissão de escrita.")
        name = request.data.get("name")
        if name is None:
            return Response(
                {"detail": "Só 'name' é editável."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        matrix.name = str(name).strip()[:256]
        matrix.save(update_fields=["name"])
        return Response(CompensationMatrixSerializer(matrix).data)

    def delete(self, request, pk):
        from fcs_parser.permissions import can_edit_experiment
        from fcs_parser.services.compensation import set_applied_compensation

        matrix = self._get_matrix(request, pk)
        if not can_edit_experiment(request.user, matrix.experiment):
            raise PermissionDenied("Descartar compensação exige permissão de escrita.")
        if matrix.is_applied:
            set_applied_compensation(matrix.experiment, None, request.user)
        matrix.is_applied = False
        matrix.active = False
        matrix.save(update_fields=["is_applied", "active"])
        return Response(status=status.HTTP_204_NO_CONTENT)
