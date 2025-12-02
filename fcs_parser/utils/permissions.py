from accounts.models import User
from fcs_parser.models import ExperimentModel


def can_edit_experiment(user: User, experiment: ExperimentModel):
    if user.is_superuser:
        return True

    memberships = user.membership_set.filter(organization=experiment.organization)
    for m in memberships:
        if m.role.permissions.filter(codename="change_experiment").exists():
            return True

    return False
