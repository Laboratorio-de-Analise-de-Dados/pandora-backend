import json
import logging
from collections import deque

import pandas as pd
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from fcs_parser.serializers import ParamListDataSerializer
from rest_framework import generics, serializers
from rest_framework.views import APIView, Response, status
from analytics.models import DashboardModel, GateModel
from analytics.serializers import DashboardSerializer, GateSerializer
from utils.density import (
    DEFAULT_COFACTOR,
    apply_gate_filter,
    compute_density,
    compute_histogram,
    default_scale,
    density_cache_key,
    get_cached_density,
    normalize_columns,
    parse_range,
    set_cached_density,
    subsample_scatter,
)

logger = logging.getLogger(__name__)


class CreateGateView(generics.CreateAPIView):
    serializer_class = GateSerializer

    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        dashboard_data = data.pop("dashboard", None)

        dashboard_serializer = DashboardSerializer(data=dashboard_data)
        dashboard_serializer.is_valid(raise_exception=True)

        dash_instance = dashboard_serializer.save()
        data["dashboard"] = dash_instance.id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        gate_instance = serializer.save()

        from analytics.tasks import recalculate_gate_analysis

        recalculate_gate_analysis(gate_instance.id)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UpdateGateView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH/DELETE /analytics/gate/<gate_id> — rename or delete a gate."""

    serializer_class = GateSerializer
    lookup_url_kwarg = "gate_id"
    queryset = GateModel.objects.all()

    def get_object(self):
        gate_id = self.kwargs.get(self.lookup_url_kwarg)
        return get_object_or_404(GateModel, pk=gate_id)

    def patch(self, request, *args, **kwargs):
        gate = self.get_object()
        update_fields = []
        new_name = request.data.get("name")
        if new_name is not None:
            gate.name = new_name
            update_fields.append("name")
        new_coords = request.data.get("gate_coordinates")
        if new_coords is not None:
            gate.gate_coordinates = new_coords
            update_fields.append("gate_coordinates")
        new_color = request.data.get("color")
        if new_color is not None:
            gate.color = new_color if new_color else None
            update_fields.append("color")
        if update_fields:
            gate.save(update_fields=update_fields)

        from analytics.tasks import recalculate_gate_analysis

        recalculate_gate_analysis(gate.id)

        if new_coords is not None:
            from utils.density import invalidate_density

            invalidate_density(gate.file_data_id)

        serializer = self.get_serializer(gate)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GetGateDataView(generics.ListAPIView):
    serializer_class = GateSerializer
    lookup_url_kwarg = "gate_id"

    def get_object(self):
        gate_id = self.kwargs.get(self.lookup_url_kwarg)
        return get_object_or_404(GateModel, pk=gate_id)

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

        for gate_in_path in gate_path:
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

        gate = get_object_or_404(GateModel, pk=gate_id)

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

        for g in gate_path:
            dataset = apply_gate_filter(dataset, g)
            if dataset.empty:
                break

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
            return Response(
                {"detail": f"Columns '{x_param}' or '{y_param}' not found in dataset."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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


class ApplyGateView(APIView):
    """POST /analytics/gate/apply — copy gates to other files (FlowJo semantics).

    Request body:
    {
      "source_gate_ids": [42],
      "target_file_data_ids": [10, 11],
      "recursive": true,           // include sub-gates (default true)
      "on_conflict": "rename"      // "rename" | "replace" | "skip"
    }
    """

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
            },
        ),
        responses=inline_serializer(
            name="ApplyGateResponse",
            fields={
                "created": serializers.IntegerField(),
                "skipped": serializers.IntegerField(),
                "details": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    def post(self, request):
        source_ids = request.data.get("source_gate_ids", [])
        target_ids = request.data.get("target_file_data_ids", [])
        recursive = request.data.get("recursive", True)
        on_conflict = request.data.get("on_conflict", "replace")

        if not source_ids or not target_ids:
            return Response(
                {"detail": "source_gate_ids and target_file_data_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_gates = list(
            GateModel.objects.filter(id__in=source_ids).select_related(
                "dashboard",
                "parent",
                "parent__parent",
                "parent__parent__parent",
            )
        )
        if len(source_gates) != len(source_ids):
            return Response(
                {"detail": "One or more source gates not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Exclude source file(s) from target list to prevent self-copy.
        source_file_ids = {g.file_data_id for g in source_gates}
        target_ids = [fid for fid in target_ids if fid not in source_file_ids]
        if not target_ids:
            return Response(
                {"detail": "No valid target files (source file excluded)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        total_created = 0
        total_skipped = 0
        details = []

        with transaction.atomic():
            for target_fd_id in target_ids:
                id_map = {}  # source gate id → new gate id
                file_created = 0
                file_skipped = 0

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
                            existing.delete()
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
                        dashboard=new_dash,
                        parent_id=new_parent_id,
                        copied_from=gate,
                        color=gate.color,
                    )
                    id_map[gate.id] = new_gate.id
                    file_created += 1

                details.append(
                    {
                        "file_data_id": target_fd_id,
                        "gates_created": file_created,
                        "gates_skipped": file_skipped,
                    }
                )
                total_created += file_created
                total_skipped += file_skipped

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
            {"created": total_created, "skipped": total_skipped, "details": details},
            status=status.HTTP_201_CREATED,
        )
