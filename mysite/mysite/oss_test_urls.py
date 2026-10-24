from django.urls import include, path

urlpatterns = [path('api/', include('notes.urls')),
               path('api/', include('feedback.urls')),
               path('', include('qa.urls')),
               path('api/', include('qa.urls'))]
