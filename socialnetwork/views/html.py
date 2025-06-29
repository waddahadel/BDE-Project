from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from fame.models import ExpertiseAreas, Fame, FameLevels
from socialnetwork import api
from socialnetwork.api import _get_social_network_user
from socialnetwork.models import SocialNetworkUsers
from socialnetwork.serializers import PostsSerializer


@require_http_methods(["GET"])
@login_required
def timeline(request):
    # initialize community mode to False the first time in the session
    if 'community_mode' not in request.session:
        request.session['community_mode'] = False

    # get extra URL parameters
    keyword = request.GET.get("search", "")
    published = request.GET.get("published", True)
    error = request.GET.get("error", None)

    user = _get_social_network_user(request.user)
    community_mode = request.session['community_mode']

    # determine post queryset
    if keyword and keyword != "":
        posts = api.search(keyword, published=published)
    else:
        posts = api.timeline(user, published=published)
        if community_mode:
            # filter to only include posts from users who share at least one community
            community_ids = user.communities.values_list("id", flat=True)
            posts = posts.filter(author__communities__id__in=community_ids).distinct()

    context = {
        "posts": PostsSerializer(posts, many=True).data,
        "searchkeyword": keyword,
        "error": error,
        "followers": list(api.follows(user).values_list('id', flat=True)),
        "community_mode": community_mode,
        "joined_communities": user.communities.all(),
        "eligible_communities": ExpertiseAreas.objects.exclude(id__in=user.communities.all()).filter(
            id__in=api.fame(user)[1].filter(fame_level__numeric_value__gte=100).values_list('expertise_area_id', flat=True)
        ),
    }

    return render(request, "timeline.html", context=context)


@require_http_methods(["POST"])
@login_required
def follow(request):
    user = _get_social_network_user(request.user)
    user_to_follow = SocialNetworkUsers.objects.get(id=request.POST.get("user_id"))
    api.follow(user, user_to_follow)
    return redirect(reverse("sn:timeline"))


@require_http_methods(["POST"])
@login_required
def unfollow(request):
    user = _get_social_network_user(request.user)
    user_to_unfollow = SocialNetworkUsers.objects.get(id=request.POST.get("user_id"))
    api.unfollow(user, user_to_unfollow)
    return redirect(reverse("sn:timeline"))


@require_http_methods(["GET"])
@login_required
def bullshitters(request):
    user = _get_social_network_user(request.user)

    # we call the bullshitters function in the api to get the bullshitters dictionary
    bs_dict = api.bullshitters()

    # now we transform the dictionary into a list of 
    bs_list = [
        {
            "expertise_area": expertise_area_label,
            "entries": entries
        }
        for expertise_area_label, entries in bs_dict.items()
    ]

    

    # we render the bullshitters html file..

    return render(request, "bullshitters.html", {"bullshitters": bs_list})

@require_http_methods(["POST"])
@login_required
def toggle_community_mode(request):
    # toggles the session-based community mode flag for the timeline view;
    # if community mode is active, switches to standard mode and vice versa
    request.session['community_mode'] = not request.session.get('community_mode', False)
    return redirect(reverse("sn:timeline"))

@require_http_methods(["POST"])
@login_required
def join_community(request):
    # get the currently logged-in social network user
    user = _get_social_network_user(request.user)

    # extract the community id from the form data (submitted via POST)
    community_id = request.POST.get("community_id")

    # retrieve the corresponding expertise area (community) object
    community = ExpertiseAreas.objects.get(id=community_id)

    # check if user has fame level of at least 100 (super pro or higher) in this area
    fame_qs = api.fame(user)[1].filter(expertise_area=community, fame_level__numeric_value__gte=100)

    # if the user is eligible (fame level >= 100), allow them to join the community
    if fame_qs.exists():
        api.join_community(user, community)

    # redirect back to the timeline regardless of result
    return redirect(reverse("sn:timeline"))

# allows the logged-in user to leave a specific community if they are a member.
# the community is identified via POST data and the user is redirected back to the timeline.
@require_http_methods(["POST"])
@login_required
def leave_community(request):
    user = _get_social_network_user(request.user)
    community_id = request.POST.get("community_id")
    community = ExpertiseAreas.objects.get(id=community_id)
    if community in user.communities.all():
        api.leave_community(user, community)
    return redirect(reverse("sn:timeline"))

# displays users similar to the current user.
# the results are passed to the 'similar_users.html' template for rendering.
@require_http_methods(["GET"])
@login_required
def similar_users(request):
    user = _get_social_network_user(request.user)
    similar = api.similar_users(user) 
    return render(request, "similar_users.html", {"similar_users": similar})
