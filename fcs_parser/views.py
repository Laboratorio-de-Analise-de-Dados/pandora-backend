from django.http import HttpResponseBadRequest
from .services import process_fcs_file
from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def process_zip(request):
    if request.method == 'POST' and request.FILES.get('file'):
        fcs_file = request.FILES['file']
        file_name = request.POST['title']
        response = process_fcs_file(fcs_file)

        return response

    return HttpResponseBadRequest('Bad Request')