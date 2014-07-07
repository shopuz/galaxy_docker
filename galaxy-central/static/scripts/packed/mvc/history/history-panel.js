define(["mvc/dataset/hda-model","mvc/dataset/hda-edit","mvc/collection/dataset-collection-edit","mvc/history/readonly-history-panel","mvc/tags","mvc/annotations","utils/localization"],function(f,b,h,d,a,c,e){var g=d.ReadOnlyHistoryPanel.extend({HDAViewClass:b.HDAEditView,initialize:function(i){i=i||{};this.selectedHdaIds=[];this.lastSelectedViewId=null;this.tagsEditor=null;this.annotationEditor=null;this.purgeAllowed=i.purgeAllowed||false;this.selecting=i.selecting||false;this.annotationEditorShown=i.annotationEditorShown||false;this.tagsEditorShown=i.tagsEditorShown||false;d.ReadOnlyHistoryPanel.prototype.initialize.call(this,i)},_setUpModelEventHandlers:function(){d.ReadOnlyHistoryPanel.prototype._setUpModelEventHandlers.call(this);this.model.on("change:nice_size",this.updateHistoryDiskSize,this);this.model.hdas.on("change:deleted",this._handleHdaDeletionChange,this);this.model.hdas.on("change:visible",this._handleHdaVisibleChange,this);this.model.hdas.on("change:purged",function(i){this.model.fetch()},this)},renderModel:function(){var i=$("<div/>");i.append(g.templates.historyPanel(this.model.toJSON()));this.$emptyMessage(i).text(this.emptyMsg);if(Galaxy&&Galaxy.currUser&&Galaxy.currUser.id&&Galaxy.currUser.id===this.model.get("user_id")){this._renderTags(i);this._renderAnnotation(i)}i.find(".history-secondary-actions").prepend(this._renderSelectButton());i.find(".history-dataset-actions").toggle(this.selecting);i.find(".history-secondary-actions").prepend(this._renderSearchButton());this._setUpBehaviours(i);this.renderHdas(i);return i},_renderTags:function(i){var j=this;this.tagsEditor=new a.TagsEditor({model:this.model,el:i.find(".history-controls .tags-display"),onshowFirstTime:function(){this.render()},onshow:function(){j.toggleHDATagEditors(true,j.fxSpeed)},onhide:function(){j.toggleHDATagEditors(false,j.fxSpeed)},$activator:faIconButton({title:e("Edit history tags"),classes:"history-tag-btn",faIcon:"fa-tags"}).appendTo(i.find(".history-secondary-actions"))})},_renderAnnotation:function(i){var j=this;this.annotationEditor=new c.AnnotationEditor({model:this.model,el:i.find(".history-controls .annotation-display"),onshowFirstTime:function(){this.render()},onshow:function(){j.toggleHDAAnnotationEditors(true,j.fxSpeed)},onhide:function(){j.toggleHDAAnnotationEditors(false,j.fxSpeed)},$activator:faIconButton({title:e("Edit history annotation"),classes:"history-annotate-btn",faIcon:"fa-comment"}).appendTo(i.find(".history-secondary-actions"))})},_renderSelectButton:function(i){return faIconButton({title:e("Operations on multiple datasets"),classes:"history-select-btn",faIcon:"fa-check-square-o"})},_setUpBehaviours:function(i){i=i||this.$el;d.ReadOnlyHistoryPanel.prototype._setUpBehaviours.call(this,i);if(!this.model){return}this._setUpDatasetActionsPopup(i);if((!Galaxy.currUser||Galaxy.currUser.isAnonymous())||(Galaxy.currUser.id!==this.model.get("user_id"))){return}var j=this;i.find(".history-name").attr("title",e("Click to rename history")).tooltip({placement:"bottom"}).make_text_editable({on_finish:function(k){var l=j.model.get("name");if(k&&k!==l){j.$el.find(".history-name").text(k);j.model.save({name:k}).fail(function(){j.$el.find(".history-name").text(j.model.previous("name"))})}else{j.$el.find(".history-name").text(l)}}})},_setUpDatasetActionsPopup:function(i){var j=this,k=[{html:e("Hide datasets"),func:function(){var l=f.HistoryDatasetAssociation.prototype.hide;j.getSelectedHdaCollection().ajaxQueue(l)}},{html:e("Unhide datasets"),func:function(){var l=f.HistoryDatasetAssociation.prototype.unhide;j.getSelectedHdaCollection().ajaxQueue(l)}},{html:e("Delete datasets"),func:function(){var l=f.HistoryDatasetAssociation.prototype["delete"];j.getSelectedHdaCollection().ajaxQueue(l)}},{html:e("Undelete datasets"),func:function(){var l=f.HistoryDatasetAssociation.prototype.undelete;j.getSelectedHdaCollection().ajaxQueue(l)}}];if(j.purgeAllowed){k.push({html:e("Permanently delete datasets"),func:function(){if(confirm(e("This will permanently remove the data in your datasets. Are you sure?"))){var l=f.HistoryDatasetAssociation.prototype.purge;j.getSelectedHdaCollection().ajaxQueue(l)}}})}k.push({html:e("Build Dataset List (Experimental)"),func:function(){j.getSelectedHdaCollection().promoteToHistoryDatasetCollection(j.model,"list")}});k.push({html:e("Build Dataset Pair (Experimental)"),func:function(){j.getSelectedHdaCollection().promoteToHistoryDatasetCollection(j.model,"paired")}});k.push({html:e("Build List of Dataset Pairs (Experimental)"),func:_.bind(j._showPairedCollectionModal,j)});return new PopupMenu(i.find(".history-dataset-action-popup-btn"),k)},_showPairedCollectionModal:function(){var i=this,j=i.getSelectedHdaCollection().toJSON().filter(function(k){return k.history_content_type==="dataset"&&k.state===f.HistoryDatasetAssociation.STATES.OK});if(j.length){require(["mvc/collection/paired-collection-creator"],function(k){window.creator=k.pairedCollectionCreatorModal(j,{historyId:i.model.id})})}else{}},_handleHdaDeletionChange:function(i){if(i.get("deleted")&&!this.storage.get("show_deleted")){this.removeHdaView(this.hdaViews[i.id])}},_handleHdaVisibleChange:function(i){if(i.hidden()&&!this.storage.get("show_hidden")){this.removeHdaView(this.hdaViews[i.id])}},_createContentView:function(j){var i=j.id,l=j.get("history_content_type"),k=null;if(l==="dataset"){k=new this.HDAViewClass({model:j,linkTarget:this.linkTarget,expanded:this.storage.get("expandedHdas")[i],selectable:this.selecting,purgeAllowed:this.purgeAllowed,hasUser:this.model.ownedByCurrUser(),logger:this.logger,tagsEditorShown:(this.tagsEditor&&!this.tagsEditor.hidden),annotationEditorShown:(this.annotationEditor&&!this.annotationEditor.hidden)})}else{if(l==="dataset_collection"){k=new h.DatasetCollectionEditView({model:j,linkTarget:this.linkTarget,expanded:this.storage.get("expandedHdas")[i],hasUser:this.model.ownedByCurrUser(),logger:this.logger})}}this._setUpHdaListeners(k);return k},_setUpHdaListeners:function(j){var i=this;d.ReadOnlyHistoryPanel.prototype._setUpHdaListeners.call(this,j);j.on("selected",function(m,l){if(!l){return}var k=[];if((l.shiftKey)&&(i.lastSelectedViewId&&_.has(i.hdaViews,i.lastSelectedViewId))){var o=i.hdaViews[i.lastSelectedViewId];k=i.selectDatasetRange(m,o).map(function(p){return p.model.id})}else{var n=m.model.id;i.lastSelectedViewId=n;k=[n]}i.selectedHdaIds=_.union(i.selectedHdaIds,k)});j.on("de-selected",function(l,k){var m=l.model.id;i.selectedHdaIds=_.without(i.selectedHdaIds,m)})},toggleHDATagEditors:function(i){var j=arguments;_.each(this.hdaViews,function(k){if(k.tagsEditor){k.tagsEditor.toggle.apply(k.tagsEditor,j)}})},toggleHDAAnnotationEditors:function(i){var j=arguments;_.each(this.hdaViews,function(k){if(k.annotationEditor){k.annotationEditor.toggle.apply(k.annotationEditor,j)}})},removeHdaView:function(j){if(!j){return}var i=this;j.$el.fadeOut(i.fxSpeed,function(){j.off();j.remove();delete i.hdaViews[j.model.id];if(_.isEmpty(i.hdaViews)){i.trigger("empty-history",i);i._renderEmptyMsg()}})},events:_.extend(_.clone(d.ReadOnlyHistoryPanel.prototype.events),{"click .history-select-btn":"toggleSelectors","click .history-select-all-datasets-btn":"selectAllDatasets","click .history-deselect-all-datasets-btn":"deselectAllDatasets"}),updateHistoryDiskSize:function(){this.$el.find(".history-size").text(this.model.get("nice_size"))},showSelectors:function(i){i=(i!==undefined)?(i):(this.fxSpeed);this.selecting=true;this.$(".history-dataset-actions").slideDown(i);_.each(this.hdaViews,function(j){j.showSelector()});this.selectedHdaIds=[];this.lastSelectedViewId=null},hideSelectors:function(i){i=(i!==undefined)?(i):(this.fxSpeed);this.selecting=false;this.$(".history-dataset-actions").slideUp(i);_.each(this.hdaViews,function(j){j.hideSelector()});this.selectedHdaIds=[];this.lastSelectedViewId=null},toggleSelectors:function(){if(!this.selecting){this.showSelectors()}else{this.hideSelectors()}},selectAllDatasets:function(i){_.each(this.hdaViews,function(j){j.select(i)})},deselectAllDatasets:function(i){this.lastSelectedViewId=null;_.each(this.hdaViews,function(j){j.deselect(i)})},selectDatasetRange:function(k,j){var i=this.hdaViewRange(k,j);_.each(i,function(l){l.select()});return i},getSelectedHdaViews:function(){return _.filter(this.hdaViews,function(i){return i.selected})},getSelectedHdaCollection:function(){return new f.HDACollection(_.map(this.getSelectedHdaViews(),function(i){return i.model}),{historyId:this.model.id})},toString:function(){return"HistoryPanel("+((this.model)?(this.model.get("name")):(""))+")"}});return{HistoryPanel:g}});