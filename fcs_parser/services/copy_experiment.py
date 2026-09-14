"""Cópia de experimento entre contextos (pessoal ↔ organização).

A cópia cria uma *análise independente* — experimento, subsamples,
amostras, dashboards, gates e resultados são linhas novas — mas reutiliza o
blob físico: o ``FileModel`` da cópia aponta para o mesmo caminho em disco
da origem e o ``zip_path`` do experimento copiado referencia o mesmo ZIP.

Nada é re-parseado e nenhum byte é duplicado; o cache Parquet da cópia é
reconstruído sob demanda a partir do ZIP compartilhado.
"""

from __future__ import annotations

from analytics.models import AnalysisResult, DashboardModel, GateModel
from fcs_parser.models import (
    ExperimentModel,
    FileDataModel,
    FileModel,
    SubsampleModel,
)


def _unique_title(title: str, user, organization_id: int | None) -> str:
    """Resolve colisão de título no destino acrescentando sufixo numérico."""
    qs = ExperimentModel.objects.filter(created_by=user, active=True)
    if organization_id is None:
        qs = qs.filter(organization__isnull=True)
    else:
        qs = qs.filter(organization_id=organization_id)
    if not qs.filter(title=title).exists():
        return title
    for i in range(2, 1000):
        candidate = f"{title}_{i}"
        if not qs.filter(title=candidate).exists():
            return candidate
    return f"{title}_{ExperimentModel.objects.count()}"


def copy_experiment(
    source: ExperimentModel,
    *,
    user,
    title: str | None = None,
    organization_id: int | None = None,
) -> ExperimentModel:
    """Clona *source* para o contexto de destino.

    ``organization_id=None`` copia para o espaço pessoal do usuário.
    O chamador é responsável por validar permissões antes de invocar.
    """
    target_title = _unique_title(
        (title or f"{source.title}_copia").strip().replace(" ", "_"),
        user,
        organization_id,
    )
    clone = ExperimentModel.objects.create(
        title=target_title,
        type=source.type,
        values=source.values,
        active=True,
        status="done" if source.status == "done" else source.status,
        file_status="uploaded",
        organization_id=organization_id,
        created_by=user,
        zip_path=source.zip_path,
        total_chunks=source.total_chunks,
    )

    file_model_map: dict[int, FileModel] = {}
    for fm in FileModel.objects.filter(experiment=source):
        file_model_map[fm.id] = FileModel.objects.create(
            experiment=clone,
            file=fm.file.name,
            file_name=fm.file_name,
            sha256=fm.sha256,
        )

    subsample_map: dict[int, SubsampleModel] = {}
    for sub in SubsampleModel.objects.filter(experiment=source):
        subsample_map[sub.id] = SubsampleModel.objects.create(
            experiment=clone,
            name=sub.name,
            source_path=sub.source_path,
            active=sub.active,
            created_by=sub.created_by,
        )

    for fd in FileDataModel.objects.filter(experiment=source):
        new_fd = FileDataModel.objects.create(
            experiment=clone,
            file=file_model_map[fd.file_id],
            file_name=fd.file_name,
            source_path=fd.source_path,
            content_guid=fd.content_guid,
            content_sha256=fd.content_sha256,
            subsample=subsample_map.get(fd.subsample_id),
            headers=fd.headers,
            data_set=None,
            parquet_path=None,
            fcs_path=fd.fcs_path,
            active=fd.active,
            deactivated_at=fd.deactivated_at,
            deactivated_by=fd.deactivated_by,
        )
        _copy_analysis(fd, new_fd)

    return clone


def _copy_analysis(source_fd: FileDataModel, new_fd: FileDataModel) -> None:
    """Clona dashboards, árvore de gates e resultados de uma amostra."""
    dashboard_map: dict[int, DashboardModel] = {}
    for dash in DashboardModel.objects.filter(file_data=source_fd):
        dashboard_map[dash.id] = DashboardModel.objects.create(
            file_data=new_fd,
            name=dash.name,
            dashboard_config=dash.dashboard_config,
        )

    gate_map: dict[int, GateModel] = {}
    pending = list(GateModel.objects.filter(file_data=source_fd))
    while pending:
        progressed = False
        for gate in pending[:]:
            if gate.parent_id is not None and gate.parent_id not in gate_map:
                continue
            new_gate = GateModel.objects.create(
                file_data=new_fd,
                name=gate.name,
                gate_coordinates=gate.gate_coordinates,
                plot_config=gate.plot_config,
                dashboard=dashboard_map[gate.dashboard_id],
                parent=gate_map.get(gate.parent_id),
                copied_from=gate,
                color=gate.color,
                created_by=gate.created_by,
            )
            GateModel.objects.filter(pk=new_gate.pk).update(created_at=gate.created_at)
            try:
                result = gate.analysis_result
            except AnalysisResult.DoesNotExist:
                result = None
            if result is not None:
                AnalysisResult.objects.create(
                    gate=new_gate, analysis_result=result.analysis_result
                )
            gate_map[gate.id] = new_gate
            pending.remove(gate)
            progressed = True
        if not progressed:
            # Ciclo ou pai fora do conjunto — não deveria acontecer; evita loop.
            break
