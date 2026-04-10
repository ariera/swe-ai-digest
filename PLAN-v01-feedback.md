I like SQLAlchemy as an ORM

data structure for digest should not asume digests will always be weekly. we may have special editions, or change the periodicity. We may skip a week and bundle two weeks together in the next run.

i wonder if we should not manually add ids to the authors in the `digest_sources.yaml` file directly, to avoid problems


--
## CLI Design


### digest
what happens if there is no content or almos not content? It would be fine to skip a digest if tehre was no content at all. And it would be fine to send a short digest if there was barely no content. What i worry is that we might have no/little content because some errors earlier in the pipeline. We may have logged a lot of warnings upstream but the pipeline does not have a way of knowing it. What are your thoughts? I don't want to overengineer a solution at this stage, but i'd like to think about this problem now. What can we do today? What is the path forward in the long term?

what's the difference between --dry-run and --no-email?

### feed
the --dry-run is not very "dry", it stores data in the DB


---

## Adapter Layer

I don't understand the point of this. If we are going to use SQLAlchemy as an ORM, should this queries not be part of the models? I'm not familiar with SQLAlchemy, so maybe i'm suggesting something unsound.


---

## Implementation Phases

I don't like the default db name `articles.db`. It no longer stores just the articles. What's a better name?


---

Let's discuss about my questions above and then create a new PLAN-v02.md that i will reivew.