from .models import Follow

def get_follow_context(cur_user, target_user) -> dict:
    return {
        "target_user": target_user,
        "followed": Follow.objects.filter(follower=cur_user, following=target_user).exists(),
        "is_it_me": cur_user == target_user,
    }

def get_like_context(cur_user, series) -> dict:
    return {
        "series": series,
        "nums_of_likes": series.likes.count(),
        "already_liked": series.likes.filter(user=cur_user).exists(),
    }

def get_save_context(cur_user, series) -> dict:
    return {
        "series": series,
        "already_saved": series.saved_by.filter(user=cur_user).exists(),
        "is_my_series": series.user_id == cur_user.id,
    }