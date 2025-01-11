from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from .forms import ProfileForm, ProjectForm
from .models import Profile, Project, Comment
from django.contrib import messages
from django.conf import settings
from customer.models import Project as CustomerProject, ProjectRequest, DeveloperRequest, MeetingRequest
from django.http import JsonResponse
from datetime import datetime, timedelta
import pytz
import json
import time
import hmac
import base64
from hashlib import sha256
from django.contrib.auth.models import User
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync




from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from .forms import ProfileForm, ProjectForm
from .models import Profile, Project
from django.contrib import messages
from django.conf import settings
from django.views.generic import CreateView, DetailView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json


def is_developer(user):
    return user.groups.filter(name=settings.DEVELOPER_GROUP).exists()

developer_required = user_passes_test(is_developer, login_url='login')

@developer_required
@login_required
def dashboard(request):
    # Get or create profile

    profile, created = Profile.objects.get_or_create(user=request.user)
    print("Profile retrieved:", profile)
    comments = Comment.objects.filter(profile=profile).order_by('-created_at')
    
    # Get customer requests
    customer_requests = DeveloperRequest.objects.filter(
        developer=profile,
        status='pending'
    ).select_related('project')
    
    # Get pending requests
    pending_requests = ProjectRequest.objects.filter(
        developer=profile,
        status='pending'
    ).select_related('project')
    
    print("Comments retrieved:", comments)
    
    context = {
        'user': request.user,
        'profile': profile,
        'comments': comments,
        'active_projects': ProjectRequest.objects.filter(
            developer=profile,
            status='accepted'
        ).select_related('project'),
        'customer_requests': customer_requests,
        'pending_requests': pending_requests,
        'meeting_requests': MeetingRequest.objects.filter(
            developer=profile,
            status='pending'
        ).select_related('customer')
    }
    return render(request, 'dev/dashboard.html', context)

@developer_required
@login_required
def profile(request):
    try:
        profile = request.user.profile
        projects = profile.projects.all().order_by('-created_at')
    except Profile.DoesNotExist:
        profile = Profile.objects.create(user=request.user)
        projects = []
    
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            
            # Check if the profile is complete
            required_fields = [
                'display_name', 'title', 'years_of_experience', 
                'hourly_rate', 'github_url', 'timezone'
            ]
            
            # Check availability fields based on type
            availability_type = form.cleaned_data['availability_type']
            if availability_type == 'weekday':
                required_fields.extend(['weekday_from', 'weekday_to'])
            elif availability_type == 'weekend':
                required_fields.extend(['weekend_from', 'weekend_to'])
            else:  # temporary
                required_fields.extend(['temp_from', 'temp_to'])
            
            is_complete = all(getattr(profile, field) for field in required_fields)
            
            if is_complete and form.cleaned_data['skills']:
                profile.is_profile_completed = True
                profile.is_verified = True  # Auto-verify when profile is complete
            
            profile.save()
            form.save_m2m()
            
            messages.success(request, 'Profile updated successfully!')
            return redirect('dev:dashboard')
    else:
        form = ProfileForm(instance=profile)
    
    return render(request, 'dev/profile.html', {
        'form': form,
        'profile': profile,
        'projects': projects
    })

def home(request):
    return render(request, 'dev/index.html')

@developer_required
@login_required
def browse_projects(request):
    projects = CustomerProject.objects.filter(status='open')
    
    # Get all pending requests for the current developer
    developer_requests = ProjectRequest.objects.filter(
        developer=request.user.profile
    ).values_list('project_id', 'status')
    
    # Create a dictionary of project_id: request_status
    request_status_dict = {proj_id: status for proj_id, status in developer_requests}
    
    # Add request status to each project
    for project in projects:
        project.request_status = request_status_dict.get(project.id)
    
    return render(request, 'dev/browse_projects.html', {
        'projects': projects
    })

@developer_required
@login_required
def request_project(request, project_id):
    project = get_object_or_404(CustomerProject, id=project_id)
    
    if request.method == 'POST':
        message = request.POST.get('message', '')
        
        # Create project request
        ProjectRequest.objects.create(
            project=project,
            developer=request.user.profile,
            message=message
        )
        
        messages.success(request, 'Project request sent successfully!')
        return redirect('dev:browse_projects')
    
    return render(request, 'dev/request_project.html', {
        'project': project
    })

@developer_required
@login_required
def view_requests(request):
    requests = DeveloperRequest.objects.filter(developer=request.user.profile)
    return render(request, 'dev/view_requests.html', {'requests': requests})

@developer_required
@login_required
def handle_request(request, request_id):
    if request.method == 'POST':
        dev_request = DeveloperRequest.objects.get(id=request_id)
        action = request.POST.get('action')
        
        if action == 'accept':
            dev_request.status = 'accepted'
            dev_request.project.status = 'in_progress'
            dev_request.project.assigned_developer = request.user.profile
            dev_request.project.save()
        elif action == 'reject':
            dev_request.status = 'rejected'
            
        dev_request.save()
        messages.success(request, f'Request {action}ed successfully!')
        
    return redirect('dev:view_requests')

@developer_required
@login_required
def project_create_api(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.developer = request.user.profile
            project.save()
            return JsonResponse({'status': 'success', 'id': project.id})
        return JsonResponse({'status': 'error', 'errors': form.errors})
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

@developer_required
@login_required
def handle_customer_request(request, request_id):
    developer_request = get_object_or_404(DeveloperRequest, id=request_id, developer=request.user.profile)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'accept':
            # Update the developer request status
            developer_request.status = 'accepted'
            
            # Update the project status and assign the developer
            project = developer_request.project
            project.status = 'in_progress'  # or whatever status you use for active projects
            project.assigned_developer = request.user.profile
            project.save()
            
            # Reject all other pending requests for this project
            DeveloperRequest.objects.filter(
                project=project,
                status='pending'
            ).exclude(id=request_id).update(status='rejected')
            
            messages.success(request, 'Request accepted successfully!')
            
        elif action == 'reject':
            developer_request.status = 'rejected'
            messages.success(request, 'Request rejected successfully!')
        
        developer_request.save()
        
    return redirect('dev:dashboard')

@developer_required
@login_required
def projects(request):
    # Get all projects for the current developer with related customer data
    projects = CustomerProject.objects.filter(
        assigned_developer=request.user.profile
    ).select_related('customer', 'customer__user').order_by('-created_at')
    
    return render(request, 'dev/projects.html', {
        'projects': projects
    })

@developer_required
@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id, profile=request.user.profile)
    return render(request, 'dev/project_detail.html', {
        'project': project
    })


# X-X-X-X-X-X-X-X-X-X-X-X-this is for dev side projects X-X-X-X-X-X-X-X-X-X-X
class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = Project
    form_class = ProjectForm
    template_name = 'dev/project_form.html'
    success_url = reverse_lazy('dev:profile')

class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'dev/project_detail.html'
    context_object_name = 'project'

class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = 'dev/project_form.html'


@require_http_methods(["POST"])
@login_required
def project_create_api(request):
    data = json.loads(request.body)
    project = Project.objects.create(
        name=data['name'],
        readme=data['readme'],
        deployed_url=data['deployed_url'],
        github_url=data['github_url'],
        client=data['client'],
        profile=request.user.profile
    )
    
    return JsonResponse({
        'id': project.id,
        'name': project.name,
        'client': project.client
    })

@require_http_methods(["POST"])
@login_required
def project_update_api(request, pk):
    project = get_object_or_404(Project, pk=pk, profile=request.user.profile)
    
    print("="*50)
    print("Received POST request for project update")
    print(f"Project ID: {pk}")
    
    try:
        # Clean up URLs if they don't have http:// or https://
        post_data = request.POST.copy()  # Make a mutable copy
        for field in ['deployed_url', 'github_url']:
            if post_data.get(field) and not post_data[field].startswith(('http://', 'https://')):
                post_data[field] = 'https://' + post_data[field]
        
        form = ProjectForm(post_data, instance=project)
        print("Form data after URL cleanup:")
        print(form.data)
        print("Form is valid:", form.is_valid())
        
        if not form.is_valid():
            print("Form errors:", form.errors)
            messages.error(request, f'Validation error: {form.errors}')
        else:
            project = form.save()
            print("Project saved successfully:", project)
            messages.success(request, 'Project updated successfully!')
            
    except Exception as e:
        print("Exception occurred:", str(e))
        print("Exception type:", type(e))
        messages.error(request, f'Error updating project: {str(e)}')
    
    return redirect('dev:dev_project_detail', pk=project.pk)

# X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X-X

@developer_required
@login_required
def my_projects(request):
    # Get all projects associated with the developer's profile
    projects = Project.objects.filter(profile=request.user.profile).order_by('-created_at')
    
    # Get statistics
    total_projects = projects.count()
    
    context = {
        'projects': projects,
        'stats': {
            'total': total_projects,
            # Remove status-based stats since your Project model doesn't have a status field
        }
    }
    return render(request, 'dev/my_projects.html', context)

@developer_required
@login_required
def customer_project_detail(request, project_id):
    project = get_object_or_404(CustomerProject, id=project_id, assigned_developer=request.user.profile)
    return render(request, 'dev/customer_project_detail.html', {
        'project': project
    })

@login_required
@developer_required
def toggle_availability(request):
    if request.method == 'POST':
        profile = request.user.profile
        duration = request.POST.get('duration')
        
        try:
            duration = int(duration)
            if duration == 0:  # Handle setting to unavailable
                profile.manual_availability = False
                profile.manual_availability_end = None
                profile.save()
                return JsonResponse({
                    'status': 'success',
                    'available': False
                })
            
            if duration < 1:
                raise ValueError
                
            profile.manual_availability = True
            profile.manual_availability_end = datetime.now(pytz.UTC) + timedelta(hours=duration)
            profile.save()
            
            return JsonResponse({
                'status': 'success',
                'available': True,
                'ends_at': profile.manual_availability_end.isoformat()
            })
            
        except (ValueError, TypeError):
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid duration provided'
            }, status=400)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

@login_required
def check_availability(request):
    profile = request.user.profile
    is_available = profile.is_currently_available()
    
    response_data = {
        'available': is_available,
    }
    
    # Add end time if manually available
    if is_available and profile.manual_availability and profile.manual_availability_end:
        response_data['ends_at'] = profile.manual_availability_end.isoformat()
    
    return JsonResponse(response_data)

def generate_zego_token(app_id, server_secret, room_id, user_id):
    timestamp = int(time.time())
    expire_time = timestamp + 3600  # Token valid for 1 hour

    payload = {
        "app_id": app_id,
        "user_id": str(user_id),
        "room_id": room_id,
        "privilege": {
            "1": 1,  # Login privilege
            "2": 1   # Publish privilege
        },
        "stream_id_list": None,
        "timestamp": timestamp,
        "expire_time": expire_time
    }

    payload_str = json.dumps(payload, separators=(',', ':'))
    signature = hmac.new(server_secret.encode('utf-8'), payload_str.encode('utf-8'), sha256).digest()
    token = base64.b64encode(signature + payload_str.encode('utf-8')).decode('utf-8')
    
    return token

@login_required
@developer_required
def handle_meeting(request, meeting_id):
    meeting = get_object_or_404(MeetingRequest, 
                              id=meeting_id, 
                              developer=request.user.profile)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'accept':
            # Generate a unique room ID
            room_id = f"meeting_{meeting.id}_{int(time.time())}"
            meeting.room_id = room_id
            meeting.status = 'accepted'
            meeting.save()

            # Send WebSocket message to notify the customer
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'chat_{meeting.chat_room.id}',  # Make sure chat_room is a field in your MeetingRequest model
                {
                    'type': 'meeting_accepted',
                    'meeting_id': meeting.id,
                    'room_id': room_id,
                    'join_url': reverse('dev:join_meeting', kwargs={'meeting_id': meeting.id})
                }
            )

            messages.success(request, 'Meeting request accepted!')
            return redirect('dev:join_meeting', meeting_id=meeting.id)
            
        elif action == 'decline':
            meeting.status = 'declined'
            meeting.save()
            
            # Notify through WebSocket
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'chat_{meeting.chat_room.id}',
                {
                    'type': 'meeting_declined',
                    'meeting_id': meeting.id
                }
            )
            
            messages.success(request, 'Meeting request declined!')
            
    return redirect('dev:meeting_requests')

@login_required
def join_meeting(request, meeting_id):
    meeting = get_object_or_404(MeetingRequest, id=meeting_id)
    user = request.user
    
    context = {
        'zego_app_id': settings.ZEGO_APP_ID,
        'zego_server_secret': settings.ZEGO_SERVER_SECRET,
        'room_id': meeting.room_id,
        'user_id': str(user.id),
        'user_name': user.get_full_name() or user.username,
    }
    
    return render(request, 'dev/meeting_room.html', context)

@login_required
@developer_required
def meeting_requests(request):
    pending_meetings = MeetingRequest.objects.filter(
        developer=request.user.profile,
        status='pending'
    ).select_related('customer')
    
    return render(request, 'dev/meeting_requests.html', {
        'pending_meetings': pending_meetings
    })

@login_required
@developer_required
def handle_meeting(request, meeting_id):
    meeting = get_object_or_404(MeetingRequest, 
                              id=meeting_id, 
                              developer=request.user.profile)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'accept':
            meeting.status = 'accepted'
            messages.success(request, 'Meeting request accepted!')
        elif action == 'decline':
            meeting.status = 'declined'
            messages.success(request, 'Meeting request declined!')
            
        meeting.save()
        
    return redirect('dev:meeting_requests')

@login_required
@developer_required
def join_meeting(request, meeting_id):
    meeting = get_object_or_404(MeetingRequest, 
                              id=meeting_id, 
                              developer=request.user.profile,
                              status='accepted')
    
    context = {
        'room_id': meeting.room_id,
        'user_name': request.user.get_full_name() or request.user.username,
    }
    
    return render(request, 'dev/meeting_room.html', context)