define(["mvc/history/history-model","mvc/collection/dataset-collection-base","mvc/dataset/hda-base","mvc/user/user-model","mvc/base-mvc","utils/localization"],function(g,k,b,a,f,d){var i=f.SessionStorageModel.extend({defaults:{expandedHdas:{},show_deleted:false,show_hidden:false},addExpandedHda:function(m){var n="expandedHdas";this.save(n,_.extend(this.get(n),_.object([m.id],[m.get("id")])))},removeExpandedHda:function(n){var m="expandedHdas";this.save(m,_.omit(this.get(m),n))},toString:function(){return"HistoryPrefs("+this.id+")"}});i.storageKeyPrefix="history:";i.historyStorageKey=function e(m){if(!m){throw new Error("HistoryPrefs.historyStorageKey needs valid id: "+m)}return(i.storageKeyPrefix+m)};i.get=function c(m){return new i({id:i.historyStorageKey(m)})};i.clearAll=function h(n){for(var m in sessionStorage){if(m.indexOf(i.storageKeyPrefix)===0){sessionStorage.removeItem(m)}}};var j=Backbone.View.extend(f.LoggableMixin).extend({HDAViewClass:b.HDABaseView,tagName:"div",className:"history-panel",fxSpeed:"fast",emptyMsg:d("This history is empty"),noneFoundMsg:d("No matching datasets found"),initialize:function(m){m=m||{};if(m.logger){this.logger=m.logger}this.log(this+".initialize:",m);this.linkTarget=m.linkTarget||"_blank";this.fxSpeed=_.has(m,"fxSpeed")?(m.fxSpeed):(this.fxSpeed);this.filters=[];this.searchFor="";this.findContainerFn=m.findContainerFn;this.hdaViews={};this.indicator=new LoadingIndicator(this.$el);this._setUpListeners();var n=_.pick(m,"initiallyExpanded","show_deleted","show_hidden");this.setModel(this.model,n,false);if(m.onready){m.onready.call(this)}},_setUpListeners:function(){this.on("error",function(n,q,m,p,o){this.errorHandler(n,q,m,p,o)});this.on("loading-history",function(){this._showLoadingIndicator("loading history...",40)});this.on("loading-done",function(){this._hideLoadingIndicator(40);if(_.isEmpty(this.hdaViews)){this.trigger("empty-history",this)}});this.once("rendered",function(){this.trigger("rendered:initial",this);return false});if(this.logger){this.on("all",function(m){this.log(this+"",arguments)},this)}return this},errorHandler:function(o,r,n,q,p){console.error(o,r,n,q,p);if(r&&r.status===0&&r.readyState===0){}else{if(r&&r.status===502){}else{var m=this._parseErrorMessage(o,r,n,q,p);if(!this.$messages().is(":visible")){this.once("rendered",function(){this.displayMessage("error",m.message,m.details)})}else{this.displayMessage("error",m.message,m.details)}}}},_parseErrorMessage:function(p,t,o,s,r){var n=Galaxy.currUser,m={message:this._bePolite(s),details:{user:(n instanceof a.User)?(n.toJSON()):(n+""),source:(p instanceof Backbone.Model)?(p.toJSON()):(p+""),xhr:t,options:(t)?(_.omit(o,"xhr")):(o)}};_.extend(m.details,r||{});if(t&&_.isFunction(t.getAllResponseHeaders)){var q=t.getAllResponseHeaders();q=_.compact(q.split("\n"));q=_.map(q,function(u){return u.split(": ")});m.details.xhr.responseHeaders=_.object(q)}return m},_bePolite:function(m){m=m||d("An error occurred while getting updates from the server");return m+". "+d("Please contact a Galaxy administrator if the problem persists")+"."},loadHistoryWithHDADetails:function(o,n,m,q){var p=function(r){return _.values(i.get(r.id).get("expandedHdas"))};return this.loadHistory(o,n,m,q,p)},loadHistory:function(p,o,n,s,q){var m=this;o=o||{};m.trigger("loading-history",m);var r=g.History.getHistoryData(p,{historyFn:n,hdaFn:s,hdaDetailIds:o.initiallyExpanded||q});return m._loadHistoryFromXHR(r,o).fail(function(v,t,u){m.trigger("error",m,v,o,d("An error was encountered while "+t),{historyId:p,history:u||{}})}).always(function(){m.trigger("loading-done",m)})},_loadHistoryFromXHR:function(o,n){var m=this;o.then(function(p,q){m.JSONToModel(p,q,n)});o.fail(function(q,p){m.render()});return o},JSONToModel:function(p,m,n){this.log("JSONToModel:",p,m,n);n=n||{};var o=new g.History(p,m,n);this.setModel(o);return this},setModel:function(n,m,o){m=m||{};o=(o!==undefined)?(o):(true);this.log("setModel:",n,m,o);this.freeModel();this.selectedHdaIds=[];if(n){this.model=n;if(this.logger){this.model.logger=this.logger}this._setUpWebStorage(m.initiallyExpanded,m.show_deleted,m.show_hidden);this._setUpModelEventHandlers();this.trigger("new-model",this)}if(o){this.render()}return this},freeModel:function(){if(this.model){this.model.clearUpdateTimeout();this.stopListening(this.model);this.stopListening(this.model.hdas)}this.freeHdaViews();return this},freeHdaViews:function(){this.hdaViews={};return this},_setUpWebStorage:function(n,m,o){this.storage=new i({id:i.historyStorageKey(this.model.get("id"))});if(_.isObject(n)){this.storage.set("exandedHdas",n)}if(_.isBoolean(m)){this.storage.set("show_deleted",m)}if(_.isBoolean(o)){this.storage.set("show_hidden",o)}this.trigger("new-storage",this.storage,this);this.log(this+" (init'd) storage:",this.storage.get());return this},_setUpModelEventHandlers:function(){this.model.hdas.on("add",this.addContentView,this);this.model.on("error error:hdas",function(n,p,m,o){this.errorHandler(n,p,m,o)},this);return this},render:function(o,p){this.log("render:",o,p);o=(o===undefined)?(this.fxSpeed):(o);var m=this,n;if(this.model){n=this.renderModel()}else{n=this.renderWithoutModel()}$(m).queue("fx",[function(q){if(o&&m.$el.is(":visible")){m.$el.fadeOut(o,q)}else{q()}},function(q){m.$el.empty();if(n){m.$el.append(n.children())}q()},function(q){if(o&&!m.$el.is(":visible")){m.$el.fadeIn(o,q)}else{q()}},function(q){if(p){p.call(this)}m.trigger("rendered",this);q()}]);return this},renderWithoutModel:function(){var m=$("<div/>"),n=$("<div/>").addClass("message-container").css({margin:"4px"});return m.append(n)},renderModel:function(){var m=$("<div/>");m.append(j.templates.historyPanel(this.model.toJSON()));this.$emptyMessage(m).text(this.emptyMsg);m.find(".history-secondary-actions").prepend(this._renderSearchButton());this._setUpBehaviours(m);this.renderHdas(m);return m},_renderEmptyMsg:function(o){var n=this,m=n.$emptyMessage(o);if(!_.isEmpty(n.hdaViews)){m.hide()}else{if(n.searchFor){m.text(n.noneFoundMsg).show()}else{m.text(n.emptyMsg).show()}}return this},_renderSearchButton:function(m){return faIconButton({title:d("Search datasets"),classes:"history-search-btn",faIcon:"fa-search"})},_setUpBehaviours:function(m){m=m||this.$el;m.find("[title]").tooltip({placement:"bottom"});this._setUpSearchInput(m.find(".history-search-controls .history-search-input"));return this},$container:function(){return(this.findContainerFn)?(this.findContainerFn.call(this)):(this.$el.parent())},$datasetsList:function(m){return(m||this.$el).find(".datasets-list")},$messages:function(m){return(m||this.$el).find(".message-container")},$emptyMessage:function(m){return(m||this.$el).find(".empty-history-message")},renderHdas:function(n){n=n||this.$el;var m=this,p={},o=this.model.hdas.getVisible(this.storage.get("show_deleted"),this.storage.get("show_hidden"),this.filters);this.$datasetsList(n).empty();if(o.length){o.each(function(r){var q=r.id,s=m._createContentView(r);p[q]=s;if(_.contains(m.selectedHdaIds,q)){s.selected=true}m.attachContentView(s.render(),n)})}this.hdaViews=p;this._renderEmptyMsg(n);return this.hdaViews},_createContentView:function(n){var m=n.id,p=n.get("history_content_type"),o=null;if(p==="dataset"){o=new this.HDAViewClass({model:n,linkTarget:this.linkTarget,expanded:this.storage.get("expandedHdas")[m],hasUser:this.model.ownedByCurrUser(),logger:this.logger})}else{o=new k.DatasetCollectionBaseView({model:n,linkTarget:this.linkTarget,expanded:this.storage.get("expandedHdas")[m],hasUser:this.model.ownedByCurrUser(),logger:this.logger})}this._setUpHdaListeners(o);return o},_setUpHdaListeners:function(n){var m=this;n.on("error",function(p,r,o,q){m.errorHandler(p,r,o,q)});n.on("body-expanded",function(o){m.storage.addExpandedHda(o)});n.on("body-collapsed",function(o){m.storage.removeExpandedHda(o)});return this},attachContentView:function(o,n){n=n||this.$el;var m=this.$datasetsList(n);m.prepend(o.$el);return this},addContentView:function(p){this.log("add."+this,p);var n=this;if(!p.isVisible(this.storage.get("show_deleted"),this.storage.get("show_hidden"))){return n}$({}).queue([function o(r){var q=n.$emptyMessage();if(q.is(":visible")){q.fadeOut(n.fxSpeed,r)}else{r()}},function m(q){var r=n._createContentView(p);n.hdaViews[p.id]=r;r.render().$el.hide();n.scrollToTop();n.attachContentView(r);r.$el.slideDown(n.fxSpeed)}]);return n},refreshContents:function(n,m){if(this.model){return this.model.refresh(n,m)}return $.when()},hdaViewRange:function(p,o){if(p===o){return[p]}var m=this,n=false,q=[];this.model.hdas.getVisible(this.storage.get("show_deleted"),this.storage.get("show_hidden"),this.filters).each(function(r){if(n){q.push(m.hdaViews[r.id]);if(r===p.model||r===o.model){n=false}}else{if(r===p.model||r===o.model){n=true;q.push(m.hdaViews[r.id])}}});return q},events:{"click .message-container":"clearMessages","click .history-search-btn":"toggleSearchControls"},collapseAllHdaBodies:function(){_.each(this.hdaViews,function(m){m.toggleBodyVisibility(null,false)});this.storage.set("expandedHdas",{});return this},toggleShowDeleted:function(m){m=(m!==undefined)?(m):(!this.storage.get("show_deleted"));this.storage.set("show_deleted",m);this.renderHdas();return this.storage.get("show_deleted")},toggleShowHidden:function(m){m=(m!==undefined)?(m):(!this.storage.get("show_hidden"));this.storage.set("show_hidden",m);this.renderHdas();return this.storage.get("show_hidden")},_setUpSearchInput:function(n){var o=this,p=".history-search-input";function m(q){if(o.model.hdas.haveDetails()){o.searchHdas(q);return}o.$el.find(p).searchInput("toggle-loading");o.model.hdas.fetchAllDetails({silent:true}).always(function(){o.$el.find(p).searchInput("toggle-loading")}).done(function(){o.searchHdas(q)})}n.searchInput({initialVal:o.searchFor,name:"history-search",placeholder:"search datasets",classes:"history-search",onfirstsearch:m,onsearch:_.bind(this.searchHdas,this),onclear:_.bind(this.clearHdaSearch,this)});return n},toggleSearchControls:function(o,m){var n=this.$el.find(".history-search-controls"),p=(jQuery.type(o)==="number")?(o):(this.fxSpeed);m=(m!==undefined)?(m):(!n.is(":visible"));if(m){n.slideDown(p,function(){$(this).find("input").focus()})}else{n.slideUp(p)}return m},searchHdas:function(m){var n=this;this.searchFor=m;this.filters=[function(o){return o.matchesAll(n.searchFor)}];this.trigger("search:searching",m,this);this.renderHdas();return this},clearHdaSearch:function(m){this.searchFor="";this.filters=[];this.trigger("search:clear",this);this.renderHdas();return this},_showLoadingIndicator:function(n,m,o){m=(m!==undefined)?(m):(this.fxSpeed);if(!this.indicator){this.indicator=new LoadingIndicator(this.$el,this.$el.parent())}if(!this.$el.is(":visible")){this.indicator.show(0,o)}else{this.$el.fadeOut(m);this.indicator.show(n,m,o)}},_hideLoadingIndicator:function(m,n){m=(m!==undefined)?(m):(this.fxSpeed);if(this.indicator){this.indicator.hide(m,n)}},displayMessage:function(r,s,q){var o=this;this.scrollToTop();var p=this.$messages(),m=$("<div/>").addClass(r+"message").html(s);if(!_.isEmpty(q)){var n=$('<a href="javascript:void(0)">Details</a>').click(function(){Galaxy.modal.show(o._messageToModalOptions(r,s,q));return false});m.append(" ",n)}return p.html(m)},_messageToModalOptions:function(q,s,p){var m=this,r=$("<div/>"),o={title:"Details"};function n(t){t=_.omit(t,_.functions(t));return["<table>",_.map(t,function(v,u){v=(_.isObject(v))?(n(v)):(v);return'<tr><td style="vertical-align: top; color: grey">'+u+'</td><td style="padding-left: 8px">'+v+"</td></tr>"}).join(""),"</table>"].join("")}if(_.isObject(p)){o.body=r.append(n(p))}else{o.body=r.html(p)}o.buttons={Ok:function(){Galaxy.modal.hide();m.clearMessages()}};return o},clearMessages:function(){this.$messages().empty();return this},scrollPosition:function(){return this.$container().scrollTop()},scrollTo:function(m){this.$container().scrollTop(m);return this},scrollToTop:function(){this.$container().scrollTop(0);return this},scrollToId:function(n){if((!n)||(!this.hdaViews[n])){return this}var m=this.hdaViews[n];this.scrollTo(m.el.offsetTop);return this},scrollToHid:function(m){var n=this.model.hdas.getByHid(m);if(!n){return this}return this.scrollToId(n.id)},toString:function(){return"ReadOnlyHistoryPanel("+((this.model)?(this.model.get("name")):(""))+")"}});var l=['<div class="history-controls">','<div class="history-search-controls">','<div class="history-search-input"></div>',"</div>",'<div class="history-title">',"<% if( history.name ){ %>",'<div class="history-name"><%= history.name %></div>',"<% } %>","</div>",'<div class="history-subtitle clear">',"<% if( history.nice_size ){ %>",'<div class="history-size"><%= history.nice_size %></div>',"<% } %>",'<div class="history-secondary-actions"></div>',"</div>","<% if( history.deleted ){ %>",'<div class="warningmessagesmall"><strong>',d("You are currently viewing a deleted history!"),"</strong></div>","<% } %>",'<div class="message-container">',"<% if( history.message ){ %>",'<div class="<%= history.status %>message"><%= history.message %></div>',"<% } %>","</div>",'<div class="quota-message errormessage">',d("You are over your disk quota"),". ",d("Tool execution is on hold until your disk usage drops below your allocated quota"),".","</div>",'<div class="tags-display"></div>','<div class="annotation-display"></div>','<div class="history-dataset-actions">','<div class="btn-group">','<button class="history-select-all-datasets-btn btn btn-default"','data-mode="select">',d("All"),"</button>",'<button class="history-deselect-all-datasets-btn btn btn-default"','data-mode="select">',d("None"),"</button>","</div>",'<button class="history-dataset-action-popup-btn btn btn-default">',d("For all selected"),"...</button>","</div>","</div>",'<div class="datasets-list"></div>','<div class="empty-history-message infomessagesmall">',d("This history is empty"),"</div>"].join("");j.templates={historyPanel:function(m){return _.template(l,m,{variable:"history"})}};return{ReadOnlyHistoryPanel:j}});