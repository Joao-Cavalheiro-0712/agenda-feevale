"""Perfis de experiência por nível de ensino (SPEC §4, §47, §65).

Um núcleo só, experiências diferentes. Quem está no 5º ano tem tema de casa e
material para levar; quem está no técnico tem relatório de laboratório e
módulo; quem faz doutorado tem qualificação, artigo e orientação. O banco é o
mesmo — o que muda é o perfil lido daqui: vocabulário, tipos de atividade
oferecidos, campos do cadastro, ordem da tela inicial, lembretes padrão e
quais recursos aparecem.

Regra: NENHUMA tela decide isso por conta própria. Toda diferença entre
níveis nasce deste módulo.
"""
from __future__ import annotations

from dataclasses import dataclass

from agenda.models import EducationType, PeriodKind

# Recursos que um perfil pode ligar.
FEATURE_GRADES = "grades"              # notas e médias
FEATURE_MATERIALS = "materials"        # "o que levar" em destaque
FEATURE_HOMEWORK = "homework"          # tema de casa como cidadão de primeira
FEATURE_INTERNSHIP = "internship"      # estágio
FEATURE_THESIS = "thesis"              # TCC / dissertação / tese
FEATURE_LAB = "lab"                    # laboratório e prática
FEATURE_GUARDIAN = "guardian"          # conta de responsável faz sentido
FEATURE_EXAM_PREP = "exam_prep"        # simulados e vestibular
FEATURE_TIMELINE = "timeline"          # linha do tempo do período
FEATURE_RESEARCH = "research"          # orientação, publicação


@dataclass(frozen=True)
class EducationProfile:
    """Tudo que muda de um nível de ensino para outro."""

    key: str
    label: str                    # como aparece na escolha do onboarding
    short: str                    # usado em frases: "na sua {short}"
    institution_label: str        # "Escola", "Instituição", "Creche"
    subject_label: str            # "Matéria", "Disciplina", "Turma"
    subject_label_plural: str
    grade_field: str              # qual campo identifica o "ano/turma/curso"
    grade_label: str              # rótulo desse campo
    default_period_kind: str
    period_options: tuple[str, ...]
    onboarding_fields: tuple[str, ...]
    event_types: tuple[str, ...]  # tipos oferecidos, na ordem que importa
    type_labels: dict[str, str]   # sobrescreve rótulos padrão
    default_type: str             # tipo assumido quando a IA não sabe
    reminder_offsets: tuple[int, ...]
    home_blocks: tuple[str, ...]  # ordem das seções da tela inicial
    features: frozenset[str]
    capture_examples: tuple[str, ...]
    tone: str = "jovem"           # jovem | familia | academico
    min_age_hint: int = 0         # idade típica mínima (avaliação de menores)


def _labels(**kwargs: str) -> dict[str, str]:
    return dict(kwargs)


_COMMON_REMINDERS = (7, 1)

PROFILES: dict[str, EducationProfile] = {
    EducationType.EARLY_CHILDHOOD.value: EducationProfile(
        key=EducationType.EARLY_CHILDHOOD.value,
        label="Educação infantil",
        short="creche ou pré-escola",
        institution_label="Escola",
        subject_label="Turma",
        subject_label_plural="Turmas",
        grade_field="grade_name",
        grade_label="Nível",
        default_period_kind=PeriodKind.ANNUAL.value,
        period_options=(PeriodKind.ANNUAL.value, PeriodKind.SEMESTER.value, PeriodKind.TRIMESTER.value),
        onboarding_fields=("institution", "grade_name", "class_name", "shift"),
        event_types=("MATERIAL", "SCHOOL_EVENT", "REMINDER", "HOMEWORK", "OTHER"),
        type_labels=_labels(
            MATERIAL="Levar para a escola", SCHOOL_EVENT="Evento da escola",
            HOMEWORK="Atividade de casa", REMINDER="Lembrete",
        ),
        default_type="REMINDER",
        reminder_offsets=(1, 0),
        home_blocks=("levar", "hoje", "proximos", "recados"),
        features=frozenset({FEATURE_MATERIALS, FEATURE_GUARDIAN}),
        capture_examples=(
            "levar fantasia na sexta",
            "reunião de pais dia 12",
            "amanhã é dia de brinquedo",
        ),
        tone="familia",
        min_age_hint=3,
    ),
    EducationType.ELEMENTARY.value: EducationProfile(
        key=EducationType.ELEMENTARY.value,
        label="Fundamental — anos iniciais",
        short="escola",
        institution_label="Escola",
        subject_label="Matéria",
        subject_label_plural="Matérias",
        grade_field="grade_name",
        grade_label="Ano",
        default_period_kind=PeriodKind.BIMESTER.value,
        period_options=(PeriodKind.BIMESTER.value, PeriodKind.TRIMESTER.value, PeriodKind.ANNUAL.value),
        onboarding_fields=("institution", "grade_name", "class_name", "shift"),
        event_types=("HOMEWORK", "MATERIAL", "EXAM", "PROJECT", "READING", "SCHOOL_EVENT", "PRESENTATION", "REMINDER"),
        type_labels=_labels(
            HOMEWORK="Tema de casa", MATERIAL="Levar para a aula", EXAM="Prova",
            PROJECT="Trabalho", READING="Leitura", SCHOOL_EVENT="Evento da escola",
            PRESENTATION="Apresentação",
        ),
        default_type="HOMEWORK",
        reminder_offsets=(2, 1),
        home_blocks=("levar", "hoje", "tarefas", "proximos", "semana"),
        features=frozenset({FEATURE_MATERIALS, FEATURE_HOMEWORK, FEATURE_GUARDIAN, FEATURE_GRADES}),
        capture_examples=(
            "tema de casa de matemática para segunda",
            "levar cartolina e cola na quarta",
            "prova de ciências sexta",
        ),
        tone="familia",
        min_age_hint=6,
    ),
    EducationType.MIDDLE_SCHOOL.value: EducationProfile(
        key=EducationType.MIDDLE_SCHOOL.value,
        label="Fundamental — anos finais",
        short="escola",
        institution_label="Escola",
        subject_label="Matéria",
        subject_label_plural="Matérias",
        grade_field="grade_name",
        grade_label="Ano",
        default_period_kind=PeriodKind.BIMESTER.value,
        period_options=(PeriodKind.BIMESTER.value, PeriodKind.TRIMESTER.value, PeriodKind.ANNUAL.value),
        onboarding_fields=("institution", "grade_name", "class_name", "shift"),
        event_types=("HOMEWORK", "EXAM", "ASSIGNMENT", "PRESENTATION", "MATERIAL", "READING", "PROJECT", "SCHOOL_EVENT", "REMINDER"),
        type_labels=_labels(
            HOMEWORK="Tarefa", ASSIGNMENT="Trabalho", EXAM="Prova",
            MATERIAL="Levar para a aula", PROJECT="Projeto",
        ),
        default_type="HOMEWORK",
        reminder_offsets=(3, 1),
        home_blocks=("hoje", "tarefas", "levar", "proximos", "semana"),
        features=frozenset({FEATURE_MATERIALS, FEATURE_HOMEWORK, FEATURE_GRADES, FEATURE_GUARDIAN}),
        capture_examples=(
            "trabalho de história em grupo para dia 20",
            "prova de matemática na sexta",
            "levar jaleco na terça",
        ),
        tone="jovem",
        min_age_hint=11,
    ),
    EducationType.HIGH_SCHOOL.value: EducationProfile(
        key=EducationType.HIGH_SCHOOL.value,
        label="Ensino médio",
        short="escola",
        institution_label="Escola",
        subject_label="Matéria",
        subject_label_plural="Matérias",
        grade_field="grade_name",
        grade_label="Série",
        default_period_kind=PeriodKind.TRIMESTER.value,
        period_options=(PeriodKind.TRIMESTER.value, PeriodKind.BIMESTER.value, PeriodKind.SEMESTER.value, PeriodKind.ANNUAL.value),
        onboarding_fields=("institution", "grade_name", "class_name", "shift"),
        event_types=("EXAM", "SIMULATION", "ASSIGNMENT", "HOMEWORK", "PAPER", "PRESENTATION", "PROJECT", "READING", "MATERIAL", "REMINDER"),
        type_labels=_labels(
            SIMULATION="Simulado", PAPER="Redação", ASSIGNMENT="Trabalho",
            HOMEWORK="Lista de exercícios", EXAM="Prova",
        ),
        default_type="ASSIGNMENT",
        reminder_offsets=(7, 3, 1),
        home_blocks=("hoje", "entregas", "provas", "proximos", "semana"),
        features=frozenset({FEATURE_GRADES, FEATURE_EXAM_PREP, FEATURE_HOMEWORK, FEATURE_TIMELINE, FEATURE_GUARDIAN}),
        capture_examples=(
            "simulado do ENEM domingo",
            "redação sobre meio ambiente para quinta",
            "prova trimestral de física dia 18",
        ),
        tone="jovem",
        min_age_hint=14,
    ),
    # EJA / supletivo. No Brasil, adulto no ensino básico não é exceção: é uma
    # modalidade inteira, com gente que trabalha o dia todo e estuda à noite.
    # Dar a essa pessoa a tela de uma criança de 8 anos — "tema de casa",
    # "levar cartolina" — é errado de produto antes de ser errado de tom. Este
    # perfil existe para que ela não precise passar pelo perfil infantil.
    EducationType.EJA.value: EducationProfile(
        key=EducationType.EJA.value,
        label="EJA / supletivo",
        short="EJA",
        institution_label="Escola ou polo",
        subject_label="Matéria",
        subject_label_plural="Matérias",
        grade_field="grade_name",
        grade_label="Etapa",
        default_period_kind=PeriodKind.SEMESTER.value,
        period_options=(
            PeriodKind.SEMESTER.value, PeriodKind.TRIMESTER.value,
            PeriodKind.MODULE.value, PeriodKind.ANNUAL.value,
        ),
        onboarding_fields=("institution", "grade_name", "shift"),
        event_types=("EXAM", "ASSIGNMENT", "HOMEWORK", "PAPER", "PRESENTATION", "READING", "REMINDER"),
        type_labels=_labels(
            ASSIGNMENT="Trabalho", HOMEWORK="Atividade", EXAM="Prova",
            PAPER="Redação", READING="Leitura",
        ),
        default_type="ASSIGNMENT",
        reminder_offsets=(7, 2, 1),
        home_blocks=("hoje", "entregas", "provas", "proximos", "semana"),
        features=frozenset({FEATURE_GRADES, FEATURE_HOMEWORK, FEATURE_TIMELINE}),
        capture_examples=(
            "prova de português na quinta à noite",
            "trabalho de história para entregar dia 20",
            "atividade de matemática para semana que vem",
        ),
        tone="academico",
        # 18: é conta de adulto para todos os efeitos — nada de tratamento de
        # menor, nada de automação desligada por padrão.
        min_age_hint=18,
    ),
    EducationType.PREP_COURSE.value: EducationProfile(
        key=EducationType.PREP_COURSE.value,
        label="Cursinho / preparatório",
        short="cursinho",
        institution_label="Curso",
        subject_label="Matéria",
        subject_label_plural="Matérias",
        grade_field="course_name",
        grade_label="Objetivo",
        default_period_kind=PeriodKind.CONTINUOUS.value,
        period_options=(PeriodKind.CONTINUOUS.value, PeriodKind.SEMESTER.value, PeriodKind.MODULE.value),
        onboarding_fields=("institution", "course_name", "shift"),
        event_types=("SIMULATION", "EXAM", "HOMEWORK", "READING", "PAPER", "REMINDER"),
        type_labels=_labels(
            SIMULATION="Simulado", HOMEWORK="Lista de exercícios", PAPER="Redação",
            EXAM="Prova", READING="Revisão",
        ),
        default_type="HOMEWORK",
        reminder_offsets=(7, 3, 1),
        home_blocks=("hoje", "provas", "entregas", "estudo", "semana"),
        features=frozenset({FEATURE_EXAM_PREP, FEATURE_TIMELINE, FEATURE_HOMEWORK}),
        capture_examples=(
            "simulado dia 15",
            "redação para sexta",
            "revisar cinemática amanhã",
        ),
        tone="jovem",
        min_age_hint=16,
    ),
    EducationType.TECHNICAL.value: EducationProfile(
        key=EducationType.TECHNICAL.value,
        label="Curso técnico",
        short="curso técnico",
        institution_label="Instituição",
        subject_label="Disciplina",
        subject_label_plural="Disciplinas",
        grade_field="course_name",
        grade_label="Curso",
        default_period_kind=PeriodKind.TRIMESTER.value,
        period_options=(PeriodKind.TRIMESTER.value, PeriodKind.MODULE.value, PeriodKind.SEMESTER.value, PeriodKind.QUADMESTER.value),
        onboarding_fields=("institution", "course_name", "module", "shift"),
        event_types=("LAB", "ASSIGNMENT", "EXAM", "PROJECT", "PRESENTATION", "INTERNSHIP", "PAPER", "MATERIAL", "ADMINISTRATIVE", "REMINDER"),
        type_labels=_labels(
            LAB="Prática de laboratório", ASSIGNMENT="Entrega técnica",
            PAPER="Relatório", PROJECT="Projeto", INTERNSHIP="Estágio",
            ADMINISTRATIVE="Prazo do curso",
        ),
        default_type="ASSIGNMENT",
        reminder_offsets=(7, 2, 1),
        home_blocks=("hoje", "entregas", "praticas", "proximos", "semana"),
        features=frozenset({FEATURE_LAB, FEATURE_INTERNSHIP, FEATURE_GRADES, FEATURE_TIMELINE}),
        capture_examples=(
            "relatório do laboratório de elétrica dia 22",
            "entrega do projeto do módulo sexta",
            "prova prática na quarta",
        ),
        tone="jovem",
        min_age_hint=15,
    ),
    EducationType.UNDERGRAD.value: EducationProfile(
        key=EducationType.UNDERGRAD.value,
        label="Graduação",
        short="faculdade",
        institution_label="Instituição",
        subject_label="Disciplina",
        subject_label_plural="Disciplinas",
        grade_field="course_name",
        grade_label="Curso",
        default_period_kind=PeriodKind.SEMESTER.value,
        period_options=(PeriodKind.SEMESTER.value, PeriodKind.TRIMESTER.value, PeriodKind.QUADMESTER.value, PeriodKind.ANNUAL.value),
        onboarding_fields=("institution", "course_name", "degree_kind", "semester", "shift"),
        event_types=("EXAM", "ASSIGNMENT", "PAPER", "SEMINAR", "PRESENTATION", "READING", "LAB", "PROJECT", "INTERNSHIP", "ADMINISTRATIVE", "REMINDER"),
        type_labels=_labels(
            PAPER="Artigo", SEMINAR="Seminário", ASSIGNMENT="Trabalho",
            READING="Leitura / fichamento", ADMINISTRATIVE="Prazo acadêmico",
            PROJECT="TCC / projeto",
        ),
        default_type="ASSIGNMENT",
        reminder_offsets=(7, 1),
        home_blocks=("hoje", "entregas", "proximos", "semana", "periodo"),
        features=frozenset({FEATURE_GRADES, FEATURE_TIMELINE, FEATURE_INTERNSHIP, FEATURE_THESIS, FEATURE_LAB}),
        capture_examples=(
            "trabalho de civil dia 23, vale 2 pontos",
            "seminário de constitucional dia 20/10",
            "prova de penal na próxima aula",
        ),
        tone="academico",
        min_age_hint=17,
    ),
    EducationType.POSTGRAD.value: EducationProfile(
        key=EducationType.POSTGRAD.value,
        label="Pós-graduação / MBA",
        short="pós",
        institution_label="Instituição",
        subject_label="Módulo",
        subject_label_plural="Módulos",
        grade_field="course_name",
        grade_label="Curso",
        default_period_kind=PeriodKind.MODULE.value,
        period_options=(PeriodKind.MODULE.value, PeriodKind.SEMESTER.value, PeriodKind.TRIMESTER.value),
        onboarding_fields=("institution", "course_name", "module", "shift"),
        event_types=("PAPER", "ASSIGNMENT", "PRESENTATION", "SEMINAR", "READING", "EXAM", "PROJECT", "ADMINISTRATIVE", "REMINDER"),
        type_labels=_labels(
            PAPER="Artigo", PROJECT="Trabalho de conclusão", ASSIGNMENT="Entrega",
            SEMINAR="Encontro", READING="Leitura",
        ),
        default_type="ASSIGNMENT",
        reminder_offsets=(14, 7, 1),
        home_blocks=("hoje", "entregas", "proximos", "periodo"),
        features=frozenset({FEATURE_THESIS, FEATURE_TIMELINE, FEATURE_RESEARCH}),
        capture_examples=(
            "entrega do artigo do módulo dia 30",
            "encontro presencial sábado",
            "leitura do capítulo 4 até quinta",
        ),
        tone="academico",
        min_age_hint=21,
    ),
    EducationType.MASTERS.value: EducationProfile(
        key=EducationType.MASTERS.value,
        label="Mestrado",
        short="mestrado",
        institution_label="Programa",
        subject_label="Disciplina",
        subject_label_plural="Disciplinas",
        grade_field="course_name",
        grade_label="Programa",
        default_period_kind=PeriodKind.SEMESTER.value,
        period_options=(PeriodKind.SEMESTER.value, PeriodKind.TRIMESTER.value, PeriodKind.ANNUAL.value),
        onboarding_fields=("institution", "course_name", "semester", "advisor"),
        event_types=("PAPER", "SEMINAR", "READING", "PRESENTATION", "ASSIGNMENT", "EXAM", "ADMINISTRATIVE", "REMINDER"),
        type_labels=_labels(
            PAPER="Artigo / dissertação", SEMINAR="Seminário", READING="Leitura dirigida",
            PRESENTATION="Qualificação / defesa", ADMINISTRATIVE="Prazo do programa",
        ),
        default_type="PAPER",
        reminder_offsets=(14, 7, 2),
        home_blocks=("hoje", "entregas", "pesquisa", "periodo"),
        features=frozenset({FEATURE_THESIS, FEATURE_RESEARCH, FEATURE_TIMELINE}),
        capture_examples=(
            "prazo de submissão do artigo dia 30",
            "reunião de orientação quinta",
            "qualificação em novembro",
        ),
        tone="academico",
        min_age_hint=22,
    ),
    EducationType.DOCTORATE.value: EducationProfile(
        key=EducationType.DOCTORATE.value,
        label="Doutorado",
        short="doutorado",
        institution_label="Programa",
        subject_label="Disciplina",
        subject_label_plural="Disciplinas",
        grade_field="course_name",
        grade_label="Programa",
        default_period_kind=PeriodKind.SEMESTER.value,
        period_options=(PeriodKind.SEMESTER.value, PeriodKind.ANNUAL.value),
        onboarding_fields=("institution", "course_name", "semester", "advisor"),
        event_types=("PAPER", "SEMINAR", "READING", "PRESENTATION", "ADMINISTRATIVE", "ASSIGNMENT", "REMINDER"),
        type_labels=_labels(
            PAPER="Artigo / tese", PRESENTATION="Qualificação / defesa",
            SEMINAR="Seminário", READING="Leitura dirigida",
            ADMINISTRATIVE="Prazo do programa",
        ),
        default_type="PAPER",
        reminder_offsets=(30, 14, 7, 2),
        home_blocks=("hoje", "entregas", "pesquisa", "periodo"),
        features=frozenset({FEATURE_THESIS, FEATURE_RESEARCH, FEATURE_TIMELINE}),
        capture_examples=(
            "submissão do capítulo 3 dia 15",
            "banca de qualificação em março",
            "encontro com a orientadora terça",
        ),
        tone="academico",
        min_age_hint=24,
    ),
    EducationType.LANGUAGE_COURSE.value: EducationProfile(
        key=EducationType.LANGUAGE_COURSE.value,
        label="Curso de idiomas",
        short="curso de idiomas",
        institution_label="Escola",
        subject_label="Turma",
        subject_label_plural="Turmas",
        grade_field="course_name",
        grade_label="Idioma e nível",
        default_period_kind=PeriodKind.MODULE.value,
        period_options=(PeriodKind.MODULE.value, PeriodKind.SEMESTER.value, PeriodKind.CONTINUOUS.value),
        onboarding_fields=("institution", "course_name", "module", "shift"),
        event_types=("HOMEWORK", "EXAM", "PRESENTATION", "READING", "REMINDER"),
        type_labels=_labels(
            HOMEWORK="Homework", EXAM="Teste", PRESENTATION="Apresentação oral",
            READING="Leitura",
        ),
        default_type="HOMEWORK",
        reminder_offsets=(3, 1),
        home_blocks=("hoje", "tarefas", "proximos"),
        features=frozenset({FEATURE_HOMEWORK}),
        capture_examples=(
            "homework unit 5 para terça",
            "teste oral na próxima aula",
        ),
        tone="jovem",
    ),
    EducationType.FREE_COURSE.value: EducationProfile(
        key=EducationType.FREE_COURSE.value,
        label="Curso livre",
        short="curso",
        institution_label="Onde",
        subject_label="Módulo",
        subject_label_plural="Módulos",
        grade_field="course_name",
        grade_label="Curso",
        default_period_kind=PeriodKind.CONTINUOUS.value,
        period_options=(PeriodKind.CONTINUOUS.value, PeriodKind.MODULE.value, PeriodKind.SEMESTER.value),
        onboarding_fields=("institution", "course_name"),
        event_types=("ASSIGNMENT", "PROJECT", "PRESENTATION", "READING", "EXAM", "REMINDER"),
        type_labels=_labels(ASSIGNMENT="Entrega", PROJECT="Projeto final"),
        default_type="ASSIGNMENT",
        reminder_offsets=(7, 1),
        home_blocks=("hoje", "entregas", "proximos"),
        features=frozenset(),
        capture_examples=("entrega do projeto final dia 30",),
        tone="jovem",
    ),
    EducationType.OTHER.value: EducationProfile(
        key=EducationType.OTHER.value,
        label="Outro",
        short="estudos",
        institution_label="Onde você estuda",
        subject_label="Matéria",
        subject_label_plural="Matérias",
        grade_field="course_name",
        grade_label="O que você estuda",
        default_period_kind=PeriodKind.SEMESTER.value,
        period_options=tuple(k.value for k in PeriodKind),
        onboarding_fields=("institution", "course_name", "shift"),
        event_types=("EXAM", "ASSIGNMENT", "HOMEWORK", "MATERIAL", "READING", "PROJECT", "REMINDER"),
        type_labels=_labels(),
        default_type="OTHER",
        reminder_offsets=_COMMON_REMINDERS,
        home_blocks=("hoje", "entregas", "proximos", "semana"),
        features=frozenset({FEATURE_GRADES}),
        capture_examples=("prova na sexta", "entrega do trabalho dia 20"),
        tone="jovem",
    ),
}

# Ordem de apresentação no onboarding — do mais novo ao mais avançado.
ONBOARDING_ORDER = (
    EducationType.EARLY_CHILDHOOD.value,
    EducationType.ELEMENTARY.value,
    EducationType.MIDDLE_SCHOOL.value,
    EducationType.HIGH_SCHOOL.value,
    EducationType.EJA.value,
    EducationType.PREP_COURSE.value,
    EducationType.TECHNICAL.value,
    EducationType.UNDERGRAD.value,
    EducationType.POSTGRAD.value,
    EducationType.MASTERS.value,
    EducationType.DOCTORATE.value,
    EducationType.LANGUAGE_COURSE.value,
    EducationType.FREE_COURSE.value,
    EducationType.OTHER.value,
)

DEGREE_LABELS = {
    "BACHELOR": "Bacharelado",
    "LICENTIATE": "Licenciatura",
    "TECHNOLOGIST": "Tecnólogo",
    "OTHER": "Outro",
}


def profile_for(education_type: str | None) -> EducationProfile:
    """Perfil do nível; cai em OTHER para valores desconhecidos ou vazios."""
    return PROFILES.get(education_type or "", PROFILES[EducationType.OTHER.value])


def profile_of_context(context) -> EducationProfile:
    return profile_for(getattr(context, "type", None))


def type_label(event_type: str, education_type: str | None = None) -> str:
    """Rótulo do tipo de atividade no vocabulário do nível."""
    from agenda.core.events import TYPE_LABELS

    profile = profile_for(education_type)
    return profile.type_labels.get(event_type) or TYPE_LABELS.get(event_type, "Compromisso")


def offered_types(education_type: str | None) -> list[tuple[str, str]]:
    """Tipos oferecidos na UI, já com o rótulo do nível."""
    profile = profile_for(education_type)
    return [(key, type_label(key, education_type)) for key in profile.event_types]


def has_feature(education_type: str | None, feature: str) -> bool:
    return feature in profile_for(education_type).features

# Níveis que, na prática, só existem para crianças. Ensino médio e técnico
# ficam de fora de propósito: aos 18 é comum e legítimo estar neles (EJA,
# repetência, quem voltou a estudar). Aqui a lista é só dos que, informados por
# uma conta adulta, indicam ou uma criança que mentiu a idade ou um pai
# organizando a agenda do filho na conta errada.
CHILD_ONLY_TYPES = (
    EducationType.EARLY_CHILDHOOD.value,
    EducationType.ELEMENTARY.value,
    EducationType.MIDDLE_SCHOOL.value,
)


def is_child_only_profile(education_type: str | None) -> bool:
    return (education_type or "") in CHILD_ONLY_TYPES


def is_minor_profile(education_type: str | None) -> bool:
    """Perfis tipicamente de crianças e adolescentes (SPEC §80, §81).

    Usado para decidir tratamento mais restritivo por padrão: nada de
    automação agressiva, retenção menor e conta de responsável disponível.
    """
    profile = profile_for(education_type)
    return 0 < profile.min_age_hint < 16
