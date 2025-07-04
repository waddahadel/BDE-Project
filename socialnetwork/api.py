from collections import defaultdict
from django.db.models import Q, Exists, OuterRef, When, IntegerField, FloatField, Count, ExpressionWrapper, Case, Value, F, Prefetch

from fame.models import Fame, FameLevels, FameUsers, ExpertiseAreas
from socialnetwork.models import Posts, SocialNetworkUsers


# general methods independent of html and REST views
# should be used by REST and html views


def _get_social_network_user(user) -> SocialNetworkUsers:
    """Given a FameUser, gets the social network user from the request. Assumes that the user is authenticated."""
    try:
        user = SocialNetworkUsers.objects.get(id=user.id)
    except SocialNetworkUsers.DoesNotExist:
        raise PermissionError("User does not exist")
    return user


def timeline(user: SocialNetworkUsers, start: int = 0, end: int = None, published=True, community_mode=False):
    """Get the timeline of the user. Assumes that the user is authenticated."""

        # T4
        # in community mode, posts of communities are displayed if ALL of the following criteria are met:
        # 1. the author of the post is a member of the community
        # 2. the user is a member of the community
        # 3. the post contains the community’s expertise area
        # 4. the post is published or the user is the author

        
    if community_mode:
        user_communities = user.communities.all() # requirement 2 implicitly satisfied from this
        if not user_communities:
            return Posts.objects.none()
        # Filter: Same expertise area must appear in user's communities, 
        # author's communities, and post's expertise areas
        posts = Posts.objects.filter(
            expertise_area_and_truth_ratings__in=user_communities, # requirement 3
            author__communities__id=F('expertise_area_and_truth_ratings__id'), # requirement 1
        ).filter(
            Q(published=published) | Q(author=user) # requirement 4
        ).distinct().order_by("-submitted")

    else:
        # in standard mode, posts of followed users are displayed
        _follows = user.follows.all()
        posts = Posts.objects.filter(
            (Q(author__in=_follows) & Q(published=published)) | Q(author=user)
        ).order_by("-submitted")
    if end is None:
        return posts[start:]
    else:
        return posts[start:end+1]


def search(keyword: str, start: int = 0, end: int = None, published=True):
    """Search for all posts in the system containing the keyword. Assumes that all posts are public"""
    posts = Posts.objects.filter(
        Q(content__icontains=keyword)
        | Q(author__email__icontains=keyword)
        | Q(author__first_name__icontains=keyword)
        | Q(author__last_name__icontains=keyword),
        published=published,
    ).order_by("-submitted")
    if end is None:
        return posts[start:]
    else:
        return posts[start:end+1]


def follows(user: SocialNetworkUsers, start: int = 0, end: int = None):
    """Get the users followed by this user. Assumes that the user is authenticated."""
    _follows = user.follows.all()
    if end is None:
        return _follows[start:]
    else:
        return _follows[start:end+1]


def followers(user: SocialNetworkUsers, start: int = 0, end: int = None):
    """Get the followers of this user. Assumes that the user is authenticated."""
    _followers = user.followed_by.all()
    if end is None:
        return _followers[start:]
    else:
        return _followers[start:end+1]


def follow(user: SocialNetworkUsers, user_to_follow: SocialNetworkUsers):
    """Follow a user. Assumes that the user is authenticated. If user already follows the user, signal that."""
    if user_to_follow in user.follows.all():
        return {"followed": False}
    user.follows.add(user_to_follow)
    user.save()
    return {"followed": True}


def unfollow(user: SocialNetworkUsers, user_to_unfollow: SocialNetworkUsers):
    """Unfollow a user. Assumes that the user is authenticated. If user does not follow the user anyway, signal that."""
    if user_to_unfollow not in user.follows.all():
        return {"unfollowed": False}
    user.follows.remove(user_to_unfollow)
    user.save()
    return {"unfollowed": True}


def submit_post(
    user: SocialNetworkUsers,
    content: str,
    cites: Posts = None,
    replies_to: Posts = None,
):
    """Submit a post for publication. Assumes that the user is authenticated.
    returns a tuple of three elements:
    1. a dictionary with the keys "published" and "id" (the id of the post)
    2. a list of dictionaries containing the expertise areas and their truth ratings
    3. a boolean indicating whether the user was banned and logged out and should be redirected to the login page
    """

    # create post  instance:
    post = Posts.objects.create(
        content=content,
        author=user,
        cites=cites,
        replies_to=replies_to,
    )

    # classify the content into expertise areas:
    # only publish the post if none of the expertise areas contains bullshit:
    _at_least_one_expertise_area_contains_bullshit, _expertise_areas = (
        post.determine_expertise_areas_and_truth_ratings()
    )
    post.published = not _at_least_one_expertise_area_contains_bullshit

    redirect_to_logout = False


    #########################
    
    # T1 – not to publish posts that have an expertise area that is contained
    # in the user fame  profile and marked negative there.
    
    # 1) Collect the expertise‑area objects detected for the post
    detected_areas = [epa["expertise_area"] for epa in _expertise_areas]

    # 2) Look up whether the author’s fame level in *any* of those areas
    #    is negative (numeric_value < 0)
    has_negative_fame = Fame.objects.filter(
        user=user,
        expertise_area__in=detected_areas,
        fame_level__numeric_value__lt=0,
    ).exists()

    # 3) If negative fame is found, force the post to stay unpublished
    if has_negative_fame:
        post.published = False
    #########################

    # T2 when users submit a negative truth rating
    # T2a when the expertise area is in the user fame profile, lower the fame level
    # T2b when the expertise area is not in the user fame profile, add an entry "Confuser" in fame profile
    # T2c when cannot lower extisting fame level, ban user.

    # find all expertise areas with negative truth ratings
    negative_areas = [negative_post for negative_post in _expertise_areas 
                  if negative_post["truth_rating"] is not None 
                  and negative_post["truth_rating"].numeric_value < 0]
    for negative_area_info in negative_areas:
        area = negative_area_info["expertise_area"]
        try:
            fame_entry = Fame.objects.get(user=user, expertise_area=area)
            current_level = fame_entry.fame_level
            # T2a:if lower level exist, lower the fame level
            try:
                lower_level = current_level.get_next_lower_fame_level()
                fame_entry.fame_level = lower_level
                fame_entry.save()
            except ValueError:
                # T2c:if there isn't a lower level, ban the user
                user.is_active = False
                user.is_banned = True
                user.save()
                Posts.objects.filter(author=user).update(published=False)
                redirect_to_logout = True
                    
        # T2b: if the expertise area is not in the user fame profile, add an entry "Confuser"
        except Fame.DoesNotExist:
            confuser_level = FameLevels.objects.filter(name__iexact="Confuser").first()
            if confuser_level:
                Fame.objects.create(
                    user=user,
                    expertise_area=area,
                    fame_level=confuser_level,
                )
    #####################


    # Task T4: Remove user from communities if fame level drops below Super Pro
    # Get user's current communities
    user_communities = user.communities.all()
    # Find the "Super Pro" fame level
    super_pro_level = FameLevels.objects.filter(name__iexact="Super Pro").first()
    
    if super_pro_level:
        for expertise_area_dict in _expertise_areas:
            expertise_area = expertise_area_dict["expertise_area"]
            # If the expertise area matches a community the user is in
            if expertise_area in user_communities:
                # Get user's fame level for this expertise area
                fame_profile = Fame.objects.filter(
                    user=user, expertise_area=expertise_area
                ).first()
                
                if fame_profile: # if level is lower than Super Pro, remove from community
                    if fame_profile.fame_level.numeric_value < super_pro_level.numeric_value:
                        user.communities.remove(expertise_area)
                    
                         
                        
    user.save()
    post.save()

    return (
        {"published": post.published, "id": post.id},
        _expertise_areas,
        redirect_to_logout,
    )


def rate_post(
    user: SocialNetworkUsers, post: Posts, rating_type: str, rating_score: int
):
    """Rate a post. Assumes that the user is authenticated. If user already rated the post with the given rating_type,
    update that rating score."""
    user_rating = None
    try:
        user_rating = user.userratings_set.get(post=post, rating_type=rating_type)
    except user.userratings_set.model.DoesNotExist:
        pass

    if user == post.author:
        raise PermissionError(
            "User is the author of the post. You cannot rate your own post."
        )

    if user_rating is not None:
        # update the existing rating:
        user_rating.rating_score = rating_score
        user_rating.save()
        return {"rated": True, "type": "update"}
    else:
        # create a new rating:
        user.userratings_set.add(
            post,
            through_defaults={"rating_type": rating_type, "rating_score": rating_score},
        )
        user.save()
        return {"rated": True, "type": "new"}


def fame(user: SocialNetworkUsers):
    """Get the fame of a user. Assumes that the user is authenticated."""
    try:
        user = SocialNetworkUsers.objects.get(id=user.id)
    except SocialNetworkUsers.DoesNotExist:
        raise ValueError("User does not exist")

    return user, Fame.objects.filter(user=user)


def bullshitters():
    """Return a Python dictionary mapping each existing expertise area in the fame profiles to a list of the users
    having negative fame for that expertise area. Each list should contain Python dictionaries as entries with keys
    ``user'' (for the user) and ``fame_level_numeric'' (for the corresponding fame value), and should be ranked, i.e.,
    users with the lowest fame are shown first, in case there is a tie, within that tie sort by date_joined
    (most recent first). Note that expertise areas with no expert may be omitted.
    """
    # we initialize the resulting dictionary

    result = {}

    # collect all  fame instances with negative fame numeric value 
    
    negative_fame_entries = Fame.objects.filter(
        fame_level__numeric_value__lt=0
    ).select_related("user", "expertise_area", "fame_level")

    # loop over the instances and construct the unsorted result dictionary.

    for fame_entry in negative_fame_entries:
        expertise_area = fame_entry.expertise_area  
        user = fame_entry.user
        numeric_fame_value = fame_entry.fame_level.numeric_value

        # we put an empty list as a placeholder to begin constructing the result,as expertise areas as keys
        
        if expertise_area not in result:
            result[expertise_area] = []

        # we append a dictionary instance with the relavant data to the list corresponding to the key (the current expertise area we are dealing with)

        result[expertise_area].append({
            "user": user,
            "fame_level_numeric": numeric_fame_value,
            "date_joined": user.date_joined,  # not part of the result format but it's used for breaking ties. will be removed later
        })

    # we sort by fame value (ascending), then by date_joined (descending)
    for ea in result:
        result[ea].sort(key=lambda entry: (
            entry["fame_level_numeric"],
            -entry["date_joined"].timestamp()
        ))
        for entry in result[ea]:
            del entry["date_joined"]  #we remove the helper key

    return result





def join_community(user: SocialNetworkUsers, community: ExpertiseAreas):
    """Join a specified community. Note that this method does not check whether the user is eligible for joining the
    community.
    """

    if community not in user.communities.all():
        user.communities.add(community)
        user.save()



def leave_community(user: SocialNetworkUsers, community: ExpertiseAreas):
    """Leave a specified community."""

    if community in user.communities.all():
        user.communities.remove(community)
        user.save()


def similar_users(user: SocialNetworkUsers):
    
    """Compute the similarity of user with all other users. The method returns a QuerySet of FameUsers annotated
    with an additional field 'similarity'. Sort the result in descending order according to 'similarity', in case
    there is a tie, within that tie sort by date_joined (most recent first)"""

    # we collect the user i's expertise areas  and fame level

    user_fame_entries = Fame.objects.filter(user=user).select_related("expertise_area", "fame_level")
    
    # construct a dictionary (mapping) with the expertise area's  id as a key and the fame level as the value

    user_fame_map = {
        entry.expertise_area.id: entry.fame_level.numeric_value
        for entry in user_fame_entries
    }
    
    # if the map is empty (user i in question have no expertise areas) then just return an empty query set

    if not user_fame_map:
        return FameUsers.objects.none()
    
    # construct a set of the expertise areas id's of user i

    Ei = set(user_fame_map.keys())
    
    # collect all other users who have fame in any of the user's expertise areas
    other_users = FameUsers.objects.exclude(id=user.id).prefetch_related(
        Prefetch('fame_set', 
                queryset=Fame.objects.filter(expertise_area__in=Ei).select_related('fame_level', 'expertise_area'),
                to_attr='relevant_fame')
    )
    
    # then we calculate the similarity for each user from the formula given in the task description

    users_with_similarity = []
    for uj in other_users:
        agreement_count = 0
        for fame in uj.relevant_fame:
            fame_ui = user_fame_map[fame.expertise_area.id]
            fame_uj = fame.fame_level.numeric_value
            if abs(fame_ui - fame_uj) <= 100:
                agreement_count += 1
        
        if agreement_count > 0:
            similarity = agreement_count / len(Ei)
            users_with_similarity.append((uj, similarity))
    
    # sort by similarity descending, then by date_joined descending
    users_with_similarity.sort(key=lambda x: (-x[1], -x[0].date_joined.timestamp()))
    
    # we create a list of user IDs in the correct order
    user_ids = [u[0].id for u in users_with_similarity]
    
    # then return a QuerySet with annotated similarity, ordered correctly
    return FameUsers.objects.filter(id__in=user_ids).annotate(
        similarity=Case(
            *[When(id=uid, then=Value(sim)) for uid, sim in 
              zip(user_ids, [u[1] for u in users_with_similarity])],
            output_field=FloatField()
        )
    ).order_by('-similarity', '-date_joined')




