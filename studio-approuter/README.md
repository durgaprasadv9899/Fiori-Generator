# Studio behind XSUAA (not deployed — needs a decision)

1. `cf create-service xsuaa application evoke-fiori-studio-xsuaa -c xs-security.json`
2. `cf bind-service fiori-generator-poc evoke-fiori-studio-xsuaa` (the studio verifies tokens with it)
3. `cf push` this folder; map the public route to the approuter only and remove the studio's own
   public route (or keep APP_ACCESS_PASSWORD off).
4. Assign the Evoke_Studio_* role collections to users in the BTP cockpit.
