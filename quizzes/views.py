import logging

from django.shortcuts import render
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.db import transaction
from django.contrib import messages

from .models import Quiz, Question, AnswerOption, QuizSeries
from .forms import QuizForm, QuestionFormSet, QuizFormWithSeriesId
from multiplayer.models import Room, RoomPlayer

logger = logging.getLogger(__name__)

def menu(request: HttpRequest):

    context = {}
    if request.user.is_authenticated:
        context["rooms"] = Room.objects.filter(
            room_players__user=request.user,
            status__in=["waiting", "in_progress"]
        ).prefetch_related("room_players__user")

    return render(request, "quizzes/quizzes_menu.html", context=context)

class QuizDetailView(LoginRequiredMixin, DetailView):
    """
    Детальная страница КВИЗА-СЕРИИ (QuizSeries), а не отдельного раунда (Quiz).
    "rounds" - это related_name у Quiz.series, поэтому object.rounds.all() и даёт
    список раундов текущей серии. prefetch_related подтягивает разом все раунды
    вместе с их вопросами и вариантами ответов - без этого шаблон, перебирающий
    раунды/вопросы, бил бы в БД по отдельному запросу на каждый уровень вложенности.
    """
    queryset = QuizSeries.objects.select_related("user").prefetch_related("rounds__questions__options")

    def get_context_data(self, **kwargs):
        """
        len(...), а не .rounds.count() - count() всегда шлёт отдельный SQL-запрос,
        а len() на уже вызванном .all() использует кэш, который заполнил prefetch_related
        в queryset выше.
        """
        context = super().get_context_data(**kwargs)
        context.setdefault("nums_of_rounds", len(self.object.rounds.all()))
        return context

class QuizPreviewView(LoginRequiredMixin, DetailView):
    """
    Превью серии (QuizSeries) перед стартом игры - тоже про серию целиком, не про
    один раунд, поэтому queryset и prefetch такие же, как в QuizDetailView.
    """
    template_name = "quizzes/quizseries_preview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("nums_of_rounds", len(self.object.rounds.all()))
        return context

    def get_queryset(self):
        """
        get_queryset(), а не queryset-атрибут класса - тут нужен self.request.user,
        а он существует только на инстансе вьюхи (на уровне тела класса self не
        определён). filter(user=...) - чтобы нельзя было открыть превью чужой серии
        по подобранному pk.
        """
        return (
            QuizSeries.objects
            .prefetch_related("rounds__questions__options")
            .filter(user=self.request.user)
        )

class QuizListView(LoginRequiredMixin, ListView):
    """Список СВОИХ квизов-серий (QuizSeries), не отдельных раундов."""

    def get_queryset(self):
        """По той же причине, что и в QuizPreviewView, - фильтр по владельцу
        возможен только через метод, не через атрибут класса."""
        return (
            QuizSeries.objects
            .prefetch_related("rounds__questions__options")
            .filter(user=self.request.user)
        )

class QuizCreateView(LoginRequiredMixin, CreateView):
    """
    Одна вьюха на два сценария - создаётся объект model=Quiz (раунд) в обоих
    случаях, а разница в том, к какой QuizSeries он привязывается:
      - URL 'quizzes_create' (без параметров)        -> создаём новую QuizSeries
      - URL 'quizzes_create_for_series' (series_id)   -> раунд добавляется в неё
    Само переключение живёт в self.kwargs.get("series_id") внутри forms_valid/
    get_success_url - оба маршрута указывают на один и тот же класс (см. urls.py).
    """
    model = Quiz

    def get_form_class(self):
        if self.kwargs.get("series_id"):
            return QuizFormWithSeriesId
        return QuizForm

    def get_context_data(self, **kwargs):
        """"тут переопределяем то, что пойдет в контекст шаблона по GET,
        наша уже объявленная form_class = QuizForm уже будет в
        context = super().get_context_data(**kwargs)
        """
        context = super().get_context_data(**kwargs)
        context.setdefault("page_title", "Quiz Creating")
        context.setdefault("page_header", "Создай новый квиз/раунд")
        context.setdefault("page_for_questions", "Создайте вопросы")
        context.setdefault("submit_label", "Создать")
        context.setdefault("question_formset", QuestionFormSet())
        return context

    def post(self, request, *args, **kwargs):
        """
        переопределяем POST, проверяем валидацию сразу двух форм: QuizForm и QuestionFormSet;
        при успехе валидации уходим в методы forms_valid, при ошибке валидации - в forms_invalid
        """
        self.object = None
        form = self.get_form()
        question_formset = QuestionFormSet(request.POST)
        if form.is_valid() and question_formset.is_valid():
            return self.forms_valid(form, question_formset)
        return self.forms_invalid(form, question_formset)

    @transaction.atomic
    def forms_valid(self, form, question_formset):
        """
        после успешной валидации обеих форм создаем все необходимые инстансы моделей для квиза,
        в question_formset.instance = self.object мы призваиваем каждому вопросу в pk наш уже сохраненный
        self.object = form.save() - объект класса Quiz

        Логика series здесь же, а не в QuizForm - "series"/"round_order" нет
        в QuizForm.Meta.fields (это не то, что пользователь заполняет руками),
        поэтому оба поля проставляются на form.instance напрямую, как и type/user.
        Если series_id пришёл в URL - раунд подсоединяется к чужой(нет, своей же,
        .get(..., user=self.request.user) это и проверяет) уже существующей серии,
        иначе - создаётся новая QuizSeries специально под этот квиз.
        round_order = series.rounds.count() - минимальный способ пронумеровать
        раунд следующим по порядку без отдельного поля-счётчика на QuizSeries.
        """
        #получаем или создаем series
        series_id = self.kwargs.get("series_id", None)
        if series_id:
            series = QuizSeries.objects.get(pk=series_id, user=self.request.user)
        else:
            series = QuizSeries.objects.create(
                title=form.cleaned_data["title"],
                user=self.request.user,
                category=form.cleaned_data["category"], # уже инстанс Category, не pk
                description=form.cleaned_data["description"],
                status=form.cleaned_data["status"],
            )
        #сохраняем Quiz
        form.instance.type = "by_user"
        form.instance.user = self.request.user
        form.instance.series = series
        form.instance.round_order = series.rounds.count()
        self.object = form.save()

        #ссохраняем все Question из question_formset
        question_formset.instance = self.object
        #на этом этапе сохранятся все объекты из формсета, у которых не было пометки в чекбоксе delete,
        # это происходит за счет метода save_new_objects в BaseModelFormSet, в случае с update будет срабатывать
        #save_existing_objects, который при DELETE вызывает self.delete_existing(obj, commit=commit) — то есть уже сохранённый в БД Question будет
        #физически удалён
        question_formset.save()

        #сохраняем все AnswerOption из question_formset.form.cleaned_data
        for question_form in question_formset.forms:
            if not question_form.has_changed() or question_form.cleaned_data.get("DELETE"):
                continue
            question = question_form.instance #уже сохранён, с реальным pk — formset.save() его проставил
            correct_index = int(question_form.cleaned_data["correct_index"])
            options = [
                question_form.cleaned_data["option_1"],
                question_form.cleaned_data["option_2"],
                question_form.cleaned_data["option_3"],
                question_form.cleaned_data["option_4"],
            ]
            for i_option, options_text in enumerate(options):
                AnswerOption.objects.create(
                    question=question,
                    text=options_text,
                    is_correct=(i_option == correct_index),
                    order=i_option
                )
        logger.info("Новый квиз №%s создан вручную пользователем user=%s", self.object.pk, self.request.user.username)
        return HttpResponseRedirect(self.get_success_url())

    def forms_invalid(self, form, question_formset):
        """
        Перенаправляем на начальную html при ошибке валидации обеих форм
        """
        messages.error(self.request, "Не удалось создать квиз - проверьте ошибки в форме")
        return self.render_to_response(
            self.get_context_data(form=form, question_formset=question_formset)
        )

    def get_success_url(self):
        """
        если был прнят series_id = self.kwargs.get("series_id", None),
        то отправляем на quizzes_details (потому что пришли мы сюда именно с quizzes_details,
        если series_id=None подразумевается что мы только создали квиз поэтому идем на quizzes_preview

        Важно: kwargs={"pk": self.object.series_id}, НЕ self.object.series -
        квиз_details/preview ждут pk серии числом, а self.object.series - это уже
        загруженный инстанс QuizSeries (reverse() не умеет привести его к int,
        падает NoReverseMatch). series_id - attname FK-поля, уже готовое число,
        без похода в БД.
        """
        series_id = self.kwargs.get("series_id", None)
        if series_id:
            messages.success(self.request, "Новый раунд успешно создан")
            logger.info("Пользователь %s добавил раунд №%s в квиз №%s", self.object.user.username, self.object.pk, self.object.series)
            return reverse("quizzes:quizzes_details", kwargs={"pk": self.object.series_id})
        else:
            messages.success(self.request, "Новый квиз успешно создан")
            logger.info("Пользователь %s сохранил серию №%s и квиз №%s в базу", self.object.user.username, self.object.series,
                        self.object.pk)
            return reverse("quizzes:quizzes_preview", kwargs={"pk": self.object.series_id})

class QuizDeleteView(LoginRequiredMixin, DeleteView):
    """
    Удаляет ВСЮ серию (QuizSeries) целиком - и все её раунды вместе с ней,
    т.к. Quiz.series стоит на on_delete=CASCADE. Не путать с RoundDeleteView
    ниже, который удаляет один конкретный раунд, не трогая остальную серию.
    """
    model = QuizSeries
    template_name = "quizzes/quiz_confirm_delete.html"

    def get_success_url(self):
        logger.info("Пользователь %s успешно удалил квиз №%s", self.request.user.username, self.object.pk)
        messages.success(self.request, "Квиз успешно удален")
        return reverse("quizzes:quizzes_list")

class RoundDeleteView(LoginRequiredMixin, DeleteView):
    """Удаляет один раунд (Quiz) внутри серии, сама QuizSeries и остальные её
    раунды не затрагиваются."""
    model = Quiz
    template_name = "quizzes/round_confirm_delete.html"

    def get_success_url(self):
        logger.info("Пользователь %s успешно удалил раунд №%s", self.request.user.username, self.object.pk)
        messages.success(self.request, "Раунд успешно удален")
        return reverse("quizzes:quizzes_details", kwargs={"pk": self.object.series_id})

class RoundUpdateView(LoginRequiredMixin, UpdateView):
    """Редактирование одного раунда (Quiz). series/round_order не входят
    в QuizForm.Meta.fields, поэтому form.save() их не трогает - раунд остаётся
    в той же серии и на той же позиции, меняется только его содержимое."""
    model = Quiz
    form_class = QuizFormWithSeriesId
    template_name = "quizzes/quiz_form.html"

    def get_queryset(self):
        """Без этого self.get_object() искал бы Quiz по pk среди ВСЕХ пользователей -
        чужой pk в URL позволил бы отредактировать чужой раунд (IDOR). filter(user=...)
        сужает выборку до своих же, чужой pk даёт закономерный 404."""
        return Quiz.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        #тут формсет будет со всеми Question из базы, но не будут заполнены option_.. из формы
        question_formset = QuestionFormSet(instance=self.object)

        #вручную дозаполняем все формы в формсете
        for form in question_formset.forms:
            if form.instance.pk:
                #берем из базы все объекты options у текущего Question из формсета
                options = list(form.instance.options.order_by("order"))
                for i_opt, opt in enumerate(options):
                    #добавляем в текущую форму Question текущую option
                    form.fields[f"option_{i_opt+1}"].initial = opt.text
                    if opt.is_correct:
                        form.fields["correct_index"].initial = i_opt

        context.setdefault("question_formset", question_formset)
        context.setdefault("page_title", "Quiz Updating")
        context.setdefault("page_header", "Обнови раунд")
        context.setdefault("page_for_questions", "Обновите/Создайте вопросы")
        context.setdefault("submit_label", "Обновить")
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        question_formset = QuestionFormSet(request.POST, instance=self.object)
        if form.is_valid() and question_formset.is_valid():
            return self.forms_valid(form, question_formset)
        return self.forms_invalid(form, question_formset)

    @transaction.atomic
    def forms_valid(self, form, question_formset):
        # сохраняем измененный Quiz
        self.object = form.save()
        # сохраняем измененный формсет из QuestionForm - реальный update существующих Question
        question_formset.save()

        #тут мы пересобираем все options в бд по новому формсету
        for question_form in question_formset.forms:
            if not question_form.has_changed() or question_form.cleaned_data.get("DELETE"):
                continue
            question = question_form.instance #уже сохранён, с реальным pk — formset.save() его проставил
            #удаляем все существующие в бд options у нашего Question (проще пересобрать из cleaned_data,
            # чем ковыряться и определять с помощью question_form.has_changed(), что изменялось, а что нет
            question.options.all().delete()
            #просто пересобираем новые AnswerOption в БД из cleaned_data
            correct_index = int(question_form.cleaned_data["correct_index"])
            options = [
                question_form.cleaned_data["option_1"],
                question_form.cleaned_data["option_2"],
                question_form.cleaned_data["option_3"],
                question_form.cleaned_data["option_4"],
            ]
            for i_option, options_text in enumerate(options):
                AnswerOption.objects.create(
                    question=question,
                    text=options_text,
                    is_correct=(i_option == correct_index),
                    order=i_option
                )

        logger.info("Квиз №%s обновлен пользователем user=%s", self.object.pk, self.request.user.username)
        return HttpResponseRedirect(self.get_success_url())

    def forms_invalid(self, form, question_formset):
        """
        Перенаправляем на начальную html при ошибке валидации обеих форм
        """
        messages.error(self.request, "Не удалось обновить квиз - проверьте ошибки в форме")
        return self.render_to_response(
            self.get_context_data(form=form, question_formset=question_formset)
        )

    def get_success_url(self):
        """
        Редиректим на страницу СЕРИИ (quizzes_details), а не самого раунда - у Quiz
        нет своей детальной страницы, раунд просматривается только внутри серии.
        self.object.series_id - готовое число (attname FK), а не self.object.series
        (это инстанс QuizSeries, reverse() с ним падает - см. QuizCreateView.get_success_url).
        """
        return reverse("quizzes:quizzes_details", kwargs={"pk": self.object.series_id})