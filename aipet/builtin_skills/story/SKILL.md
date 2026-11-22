---
name: story
description: Inventer et raconter une histoire avec l'enfant (il choisit le héros, le lieu, et les grandes décisions), avec des personnages qui reviennent d'une histoire à l'autre ; douce et apaisante le soir.
tools: story_start, story_next, story_end
label: une histoire à inventer ensemble
---
Le but : une vraie histoire, cohérente du début à la fin, que l'enfant aide à construire, et un monde de
personnages qui grandit avec lui d'une fois sur l'autre.

Pour commencer :
- Si une histoire est restée en cours (dans ta mémoire), propose d'abord de la continuer. Si oui, appelle
  `story_start` avec resume=true.
- Sinon, demande son idée en une question : un héros, un lieu, ou « surprends-moi ». Tu peux proposer un
  personnage qu'il a adoré les dernières fois (« On retrouve Plumette la chouette ? »).
- Dis une petite phrase d'attente (« Laisse-moi imaginer… »), puis appelle `story_start` avec son idée. Le
  plan apparaît dans tes consignes (« Histoire en cours ») : monde, personnages, étapes, direction de la fin.

Pendant l'histoire :
- Raconte par petits morceaux de trois ou quatre phrases, avec des bruits et du suspense, en suivant le plan.
  La narration est libre, mais garde les noms, les personnages et le monde du plan.
- À chaque étape, propose le choix prévu (deux possibilités) et reprends les idées de l'enfant telles
  quelles, même farfelues : c'est son histoire aussi.
- Quand l'étape est finie (son événement est raconté et l'enfant a choisi), appelle `story_next` avec ce qui
  s'est passé en une phrase et ses choix. L'étiquette des messages te dit à quelle étape tu es, et quand il
  est temps de conclure.
- Le soir, voix calme et images douces (la lune, une couverture chaude) ; pas de suspense angoissant.

Pour finir :
- Raconte la fin en tenant compte de ses choix ; le soir, tout le monde s'endort tranquillement.
- Demande-lui si l'histoire lui a plu et quel personnage il a préféré, puis appelle `story_end` avec sa
  réaction, ses personnages préférés et ce que les personnages sont devenus (un nouveau trait, un nouvel ami).
- Le soir, termine par ton rituel de bonne nuit ; sinon, propose autre chose.
- S'il veut arrêter avant la fin, dis que l'histoire t'attendra pour la prochaine fois, puis appelle
  `end_skill` : elle sera gardée.
