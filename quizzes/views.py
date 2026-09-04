import logging

from django.shortcuts import render
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.db import transaction
from django.contrib import messages

from .models import Quiz, Question, AnswerOption
from .forms import QuizForm, QuestionFormSet
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
    queryset = Quiz.objects.select_related("user").prefetch_related("questions__options")

class QuizPreviewView(LoginRequiredMixin, DetailView):
    model = Quiz
    template_name = "quizzes/quiz_preview.html"

class QuizListView(LoginRequiredMixin, ListView):

    def get_queryset(self):
        return (
            Quiz.objects
            .prefetch_related("questions__options")
            .filter(user=self.request.user)
        )

class QuizCreateView(LoginRequiredMixin, CreateView):
    model = Quiz
    form_class = QuizForm

    def get_context_data(self, **kwargs):
        """"тут переопределяем то, что пойдет в контекст шаблона по GET,
        наша уже объявленная form_class = QuizForm уже будет в
        context = super().get_context_data(**kwargs)
        """
        context = super().get_context_data(**kwargs)
        context.setdefault("page_title", "Quiz Creating")
        context.setdefault("page_header", "Создай новый квиз")
        context.setdefault("page_for_questions", "Создайте вопросы")
        context.setdefault("submit_label", "Создать")
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
        """
        #сохраняем Quiz
        form.instance.type = "by_user"
        form.instance.user = self.request.user
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
        при успешной валидации и создании всех необходимых инстансов модели,
        перенаправляем в quizzes:quizzes_detail
        """
        return reverse("quizzes:quizzes_preview", kwargs={"pk": self.object.pk})

class QuizDeleteView(LoginRequiredMixin, DeleteView):
    model = Quiz

    def get_success_url(self):
        logger.info("Пользователь %s успешно удалил квиз №%s", self.request.user.username, self.object.pk)
        messages.success(self.request, "Квиз успешно удален")
        return reverse("quizzes:quizzes_list")

class QuizUpdateView(LoginRequiredMixin, UpdateView):
    model = Quiz
    form_class = QuizForm
    template_name = "quizzes/quiz_form.html"

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
        context.setdefault("page_header", "Обнови квиз")
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
        при успешной валидации и создании всех необходимых инстансов модели,
        перенаправляем в quizzes:quizzes_detail
        """
        return reverse("quizzes:quizzes_details", kwargs={"pk": self.object.pk})